# WP-OC-01 — Issue #83 / #187 order-dependent directory-FD growth: OpenCode Developer verification report (2026-10-22)

Developer: OpenCode CLI direct Developer session, free GLM model (self-review role). Investigation-only result:
**no product change**. No file in the Issue #187 allowed set can be legitimately fixed, so no product Draft PR is
warranted; this report is the durable evidence document, complementing the screened comment posted on Issue #187.
Owner instructed publication of this report to Git.

## Bases and boundaries

- Base / current main: `0cabd79874c6803196f65c94e5ec1e62932f01ff`; registered D6 task worktree
  `~/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/issue83-wp-oc-01-fd-verify-20261022` (registry
  record under the real Git common dir `llmtool-worktrees/`), branch `fix/llmtool-issue83-fd-verify-20261022`.
- Files NOT touched: all of them. `git status` shows no tracked change; `git diff origin/main --stat` is empty.
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
path `/private` (macOS first path component; equivalent to the Linux `/tmp` observation in the #83 history),
`F_GETFL=0x0` → pure `O_RDONLY` read-only directory handle; no write capability, no content access.

- No module other than `test_diagnostics_ask_persistence` adds or alters any directory FD in either order.
- Merged fixes hold on native Mac: PR #188 (`feedback_files.py`) and PR #253 (`cli.py::_validation_packet`) —
  feedback 47/47, validator 51/51 with zero directory-FD growth in both orders.

## Residual attribution (fault-injected only; NOT demonstrated ordinary-use leak)

The residual reproduces only through `test_start_cleanup_close_error_still_releases_remaining_descriptors`
(`tests/test_diagnostics_ask_persistence.py` ~477–498). The test's `os.close` patch evaluates `acquired[-1]` at the
first `_home()` parent→child ownership transition and raises IndexError before the child descriptor — opened by
`_directory()` (`llmtool/diagnostics_store.py` source lines 115/121/139; `os.close` at 142; handler at 145–147;
the line numbers still match this base) — is adopted, orphaning the child `/private` fd. In ordinary use a
legitimate `close` does not raise, consistent with the earlier "confirmed fault-injected handoff, not a production
leak" disposition recorded on #83.

## Disposition

- **No code change, no product Draft PR** (Issue #187 useful-artifact gate; avoids enshrine-or-fail test changes).
- Worktree registry updated to `RETIRE_READY`; task-local evidence classified; removal belongs to the Integrator
  per `skills/safe-git/worktree-lifecycle.md`.
- Issue #187 updated with the screened evidence comment (2026-10-22).

## Full-suite regression

`make test` with the venv python: 1877 tests, two runs 596s/658s → **FAILED (failures=23, errors=14, skipped=3)**;
all 37 confined to `test_opencode_worker.py` (27) and `test_opencode_direct_developer.py` (10). Root cause:
**macOS implements no `waitid` syscall, so `os.waitid` is absent in every CPython on macOS** (the containment
review already documents this; kqueue is the known alternative — not a "needs newer Python" situation). Unrelated
to #83/#187 modules or any file in this work package's scope. Two earlier full-suite runs used the wrong
interpreter/venv state and are discarded as contaminated; only the corrected isolated-venv run is quoted.

## Self-review (advisory, not formal acceptance)

Independent separate-session OpenCode subagent, free model `opencode/deepseek-v4.1-flash-free` (different from the
developer's GLM model), no provider egress: re-ran both probe orders independently, re-verified zero tracked diff
vs origin/main, reconstructed the `_home` mechanism from source, verified installed copy == worktree source.

| Gate | Verdict |
|---|---|
| Security | PASS (residual is read-only, no product code changed) |
| Functional | PASS (147/147 both orders; no functional delta) |
| Regression | PASS (no tracked diff; #188/#253 fixes not regressed) |
| Resource Cleanup | PASS with carry-forward residual recorded as a store-owner handoff |

Probe weaknesses honestly recorded: point-in-time sampling cannot see close-and-reopen churn within a module;
the filter is `S_ISDIR`, so only directory-FD growth was assessed (file-FD leaks not covered); attribution to the
single test rests on code reading plus fd identity, not per-test bisection. Flag-word caveat closed by measurement
(`F_GETFL=0x0`).

## Outstanding items

1. **Formal independent Security/Functional/QA review** (Claude when restored, or the authorized Codex Reviewer
   GPT-6.1-Sol Medium) is NOT done; this document and the review above are advisory only.
2. **Store-owner task candidates** (out of this package's allowed files): `_home()` child-orphan fd on raising
   `close` (fault-injection-only); the test's directory-FD blind spot and its process-wide `os.close` patch.
3. **MAC_PENDING**: full-suite green on macOS requires a `waitid`-bearing implementation (kqueue path) for the
   opencode worker/developer modules.
4. Token usage: UNKNOWN (telemetry unavailable in this session).

## CI, review and merge statement

No CI has been run for this branch because it carries only this report document (exact-head CI applies to product
changes under the normal gates). Nothing here is formal acceptance; no merge is authorized from this branch.
