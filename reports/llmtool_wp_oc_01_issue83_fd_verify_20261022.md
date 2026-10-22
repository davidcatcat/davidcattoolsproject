# WP-OC-01 — Issue #83 / #187 order-dependent directory-FD growth: OpenCode Developer verification report (2026-10-22)

Developer: OpenCode CLI direct Developer session, free GLM model (self-review role). Investigation-only result:
**no product change**. No file in the Issue #187 allowed set can be legitimately fixed, so no product Draft PR is
warranted; this report is the durable evidence document, complementing the screened comment posted on Issue #187.
Owner instructed publication of this report to Git.

## Bases and boundaries

- Base / current main: `0cabd79874c6803196f65c94e5ec1e62932f01ff`; registered D6 task worktree
  `~/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/issue83-wp-oc-01-fd-verify-20261022` (registry
  record under the real Git common dir `llmtool-worktrees/`), branch `fix/llmtool-issue83-fd-verify-20261022`.
- Product/test files: none changed. At investigation time (base `0cabd79`, 2026-10-22) `git status` showed no
  tracked change and `git diff origin/main --stat` was empty; the branch since adds only this report file.
- Forbidden-owner boundaries respected: `diagnostics_store.py`/`diagnostics_reader.py`/`doctor.py` (store/reader/doctor
  handoff, closed PR #180), `retrieval*` (PR #179), `cli.py` (PR #253 scope), M2/Worker/Host authority code.
- Runtime: task-local venv, macOS Python 3.9.6 with pinned PyYAML 6.0.3 (requirements-ci pin), prompt-toolkit
  3.0.52 + wcwidth 0.9.2 for tui coverage, llmtool installed `--no-deps` from the task tree; installed copy verified
  IDENTICAL to worktree source by the reviewer.

## Reproduction (shared interpreter, native Mac; read-only probe over /dev/fd + fstat + F_GETPATH + F_GETFL)

Modules in #83 order: `test_diagnostics_ask_persistence` (33), `test_feedback` (47),
`test_qg_qa1_file_context` (16), `test_validator` (51).

| Order | Directory-FD counts | Tests |
|---|---|---|
| Forward | 0 → 1 → 1 → 1 → 1 | 147/147 PASS, exit 0 |
| Reversed (validator first) | 0 → 0 → 0 → 0 → 1 | 147/147 PASS, exit 0 |

Single residual descriptor in both orders: fd, dev `16777223`, inode `135581948`, uid `0`, mode `S_IFDIR`,
path `/private` (macOS first path component; analogous to the Linux `/tmp` observation in the #83 history),
`F_GETFL=0x0` → `O_RDONLY` read-only directory descriptor: it confers no write capability, but as a directory
handle it still permits listing and `*at()`-relative lookups under `/private`.

- No module other than `test_diagnostics_ask_persistence` adds or alters any directory FD in either order.
- Merged fixes hold on native Mac: PR #188 (`feedback_files.py`) and PR #253 (`cli.py::_validation_packet`) —
  feedback 47/47, validator 51/51 with zero directory-FD growth in both orders.

## Residual attribution (fault-injected only; NOT demonstrated ordinary-use leak)

**Evidence levels.** The original OpenCode probe was module-level (directory-FD counts sampled between modules)
and was not bisected per test. A per-test probe was added afterwards (Claude Developer, 2026-10-22, same worktree
and venv Python 3.9.6, cwd a scratch directory, `python -I -B`): the 33 tests of
`tests/test_diagnostics_ask_persistence.py` were run one by one, in loader order, in one interpreter, sampling
directory FDs (`/dev/fd` + `fstat` + `S_ISDIR`) before and after each test. Exactly one test added a directory FD:
`PersistedAskTests.test_start_cleanup_close_error_still_releases_remaining_descriptors` (new fd with dev
`16777223`, inode `135581948`, i.e. the same `/private` descriptor as above; the test itself passed). No other
test in that module changed the directory-FD set. This is per-test sampling in one order, not an exhaustive
isolation of every test combination.

**Mechanism (code-reading attribution, not separately instrumented).** In that test
(`tests/test_diagnostics_ask_persistence.py` ~477–498) the `os.close` patch evaluates `acquired[-1]` at the first
`_home()` parent→child ownership transition and, by code reading, raises IndexError before the child descriptor —
opened by `_directory()` (`llmtool/diagnostics_store.py` source lines 115/121/139; `os.close` at 142; handler at
145–147; the line numbers still match this base) — is adopted, orphaning the child `/private` fd. This exception
path was not traced at runtime. The leak requires `close` to raise inside that handoff; in ordinary operation
`close(2)` on such a descriptor is not expected to fail, but `close(2)` can fail in principle (for example `EINTR`
or `EIO`) and this report did not test such real failures. The finding therefore stays consistent with the earlier
"confirmed fault-injected handoff, not a demonstrated production leak" disposition recorded on #83.

## Disposition

- **No code change, no product Draft PR** (Issue #187 useful-artifact gate; avoids enshrine-or-fail test changes).
- Worktree registry updated to `RETIRE_READY`; task-local evidence classified; removal belongs to the Integrator
  per `skills/safe-git/worktree-lifecycle.md`.
- Issue #187 updated with the screened evidence comment (2026-10-22).

## Full-suite regression

Original OpenCode record: `make test` with the venv python, 1877 tests, two runs (596s / 658s), reported as
**FAILED (failures=23, errors=14, skipped=3)**. The original notes did not record the counts of each run
separately, so whether *both* runs had exactly these counts is **UNKNOWN**. Two still earlier full-suite runs used
the wrong interpreter/venv state and are discarded as contaminated.

Independent rerun (Claude Developer, 2026-10-22, this worktree at `fca6216`, whose product code equals base
`0cabd79`; `make test PYTHON=.venv/bin/python`, venv on Apple CommandLineTools CPython 3.9.6, macOS 15.7.9
x86_64): **Ran 1877 tests in 698.6s — FAILED (failures=23, errors=14, skipped=3)**; all 37 in
`test_opencode_worker.py` (27) and `test_opencode_direct_developer.py` (10). None are in the #83/#187 modules or
any file in this work package's scope.

Cause, scoped to the tested interpreter: Apple CLT CPython 3.9.6 does **not expose the `os.waitid` binding**,
while the platform libc does provide `waitid`. Probe on this Mac (`python -I -B`, scratch cwd):

| Interpreter | `hasattr(os, 'waitid')` | `ctypes.CDLL(None)` has `waitid` |
|---|---|---|
| `/Library/Developer/CommandLineTools/usr/bin/python3` 3.9.6 | False | True |
| `/usr/local/opt/python@3.13/bin/python3.13` 3.13.3 | True | True |

Failure evidence from the rerun log: 7 errors `AttributeError: <module 'os' from
'/Library/Developer/CommandLineTools/.../python3.9/os.py'> does not have the attribute 'waitid'` (tests patching
`os.waitid`), and most failures report `OPENCODE_CLEANUP_UNKNOWN` instead of the expected code (e.g. 9 ×
`'OPENCODE_CLEANUP_UNKNOWN' != 'OPENCODE_OUTPUT_RETURNED'`), which is the worker's fail-closed result when the
non-reaping exit observation primitive is missing. A minority are timing assertions (e.g. one
`'OPENCODE_TIMEOUT' != 'OPENCODE_OUTPUT_LIMIT'`); PR #270 attributes those to fixture launch latency on this
interpreter rather than to `waitid`. This report makes no claim about other macOS CPython builds beyond the two
probed above.

## Self-review (advisory, not formal acceptance)

Advisory reviewer: an OpenCode subagent session started by the OpenCode Developer session, free model
`opencode/deepseek-v4.1-flash-free` (different from the developer's GLM model). It is not the formal Reviewer and
not independent of the Developer's authority; its session identifier was not recorded (UNKNOWN). Egress: its model
inference ran on the remote OpenCode free provider, so its prompts and the repository content it read left this Mac
for that provider; no other external egress was reported, and no credentials or private data were in the task's
file set (as reported by the Developer session; not independently audited here). It re-ran both probe orders
independently, re-verified zero tracked diff vs origin/main, reconstructed the `_home` mechanism from source, and
verified installed copy == worktree source.

| Gate | Verdict |
|---|---|
| Security | PASS (residual is read-only, no product code changed) |
| Functional | PASS (147/147 both orders; no functional delta) |
| Regression | PASS (no tracked diff; #188/#253 fixes not regressed) |
| Resource Cleanup | PASS with carry-forward residual recorded as a store-owner handoff |

Probe weaknesses honestly recorded: point-in-time sampling cannot see close-and-reopen churn within a module;
the filter is `S_ISDIR`, so only directory-FD growth was assessed (file-FD leaks not covered); attribution to the
single test originally rested on code reading plus fd identity; the later per-test probe (see Residual
attribution) now supports it at test granularity, while the internal exception path remains code-reading only.
Flag-word caveat closed by measurement (`F_GETFL=0x0`).

## Outstanding items

1. **Formal independent Security/Functional/QA review** (Claude when restored, or the authorized Codex Reviewer
   GPT-6.1-Sol Medium) is NOT done; this document and the review above are advisory only.
2. **Store-owner task candidates** (out of this package's allowed files): `_home()` child-orphan fd on raising
   `close` (fault-injection-only); the test's directory-FD blind spot and its process-wide `os.close` patch.
3. **MAC_PENDING**: the full suite is not green on the tested Apple CLT CPython 3.9.6 interpreter because of the
   missing `os.waitid` binding (opencode worker/developer modules). Possible directions include a ctypes binding to
   the libc `waitid` (as proposed in PR #270), a kqueue-based exit observation, or requiring an interpreter that
   exposes `os.waitid`; this report does not prescribe a fix, which belongs to that separate work package.
4. Token usage: UNKNOWN (telemetry unavailable in this session).

## CI, review and merge statement

No CI has been run for this branch because it carries only this report document (exact-head CI applies to product
changes under the normal gates). Nothing here is formal acceptance; no merge is authorized from this branch.

## Revision note (2026-10-22)

Revised by Claude Developer (Owner-authorized takeover; OpenCode usage exhausted) to address the REVISE review on
PR #266 HEAD `fca6216`: scoped the `waitid` claim to the tested interpreter with probe and failure evidence;
removed the prescriptive "kqueue path" wording; added per-test FD evidence and labelled the mechanism as
code-reading; corrected scope, `/private` descriptor capability, "analogous", `close(2)`, egress, advisory-reviewer
identity and full-suite run-count statements. Docs-only; no product or test file changed.
