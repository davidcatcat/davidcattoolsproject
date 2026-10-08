# One-use ai-loop helper maintenance: Issue #52

Status: **BOOTSTRAP CANDIDATE — independent review and full Mac/CI acceptance pending**. Do not run C1 while this maintenance task is in progress.

## Identity and scope

- Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`, origin `davidcatcat/davidcattoolsproject`.
- Existing Mac local branch: `automation/ai-loop` at exact pre-maintenance HEAD `ac03992b6025a2f1fbea786be4b9823ecbef325f`.
- Local **uncommitted** repair path **only**: `scripts/test_ai_loop.py`, previously changed by Codex (+66/-10) and 210 test cases passed with `TMPDIR=/private/var/tmp` on Python 3.13.
- Preserve current C1 `automation/ai-loop/task.json` and `task.md`, historic `76644` files, unrelated work, stash, runner safety checks and all negative assertions.
- Target publication: existing Draft PR #45 / `automation/ai-loop` control branch. No new feature branch, no merge, no C1 execution.

## Read-only independent Claude review contract

Independently inspect the **exact supplied uncommitted diff and source byte SHA-256** from `scripts/test_ai_loop.py`. Read ONLY the relevant immutable historic recovery fixture, active C1 task, loader schema, and test helpers. Verify:

1. The historical 76644 fixture has been decoupled from the mutable active C1 task.
2. The active C1 contract continues to load independently and does not become recovery authority.
3. Existing positive and **negative** recovery assertions remain strict: no unexpected paths/scenarios, duplicate recovery, wrong hashes/provenance, spoofed evidence, or missing process proof.
4. Only `scripts/test_ai_loop.py` changed, and no unrelated runner source, task, security gate, application code, or CI bypass is in the candidate diff.
5. New regression tests are meaningful, and no false PASS is possible.
6. Physical macOS temporary root is used for deterministic test fixtures. Do **not** weaken symlink/path-escape security to make default `/var` temporary paths pass.
7. All review claims are tied to the specific SHA supplied; absence of evidence means FAIL.

Return a strict structured PASS only if all checks are sufficiently supported, with `reviewed_sha256` equal to the supplied source digest and `findings=[]`. Otherwise return FAIL with concrete actionable findings. Review **read-only**, no code edits, shell/tool execution except bounded Read/Glob/Grep, model delegation or Git mutation.

## Bounded Codex repair if the first reviewer FAILS

Only one repair attempt, only `scripts/test_ai_loop.py`; preserve all security assertions. Use review findings and archived fixture, no speculative refactor. Outer script must re-run all 210 helper cases, and Claude must review the **new** exact source hash again. On repeated FAIL, stop with preserved logs; no manual prompt relay.

## Deterministic gates and publication

- Local exact-candidate test: Python 3.13 `scripts/test_ai_loop.py` with physically validated `TMPDIR=/private/var/tmp`, all 210 cases pass; diff check and one tracked-file allowlist hold before/after models.
- Publish only after review PASS, by creating a commit on the current Git remote tree **without** overwriting the dirty Mac worktree. Remote branch fast-forward push must succeed; concurrent updates stop instead of force-push.
- Wait for GitHub **ai-loop helper CI** on exact published HEAD. Both `helper (3.9)` and `helper (3.14)` must PASS. Pending/FAIL/missing evidence stops; never infer PASS.
- Only after exact-head CI PASS and remote bytes identical to private candidate snapshot, reconcile the canonical local branch by a guarded fast-forward; then return to clean main, without starting C1. Preserve private snapshot and original stash.
- A process crash/interruption must preserve evidence and reuse a frozen published marker, **not** re-invoke models after publication.
- Keep operator interface one **named ai-loop script option**. This handoff alone does not imply the script is executable, reviewed, or ready.

No automatic merge or product Git ADD. Security Review and Functional QA of the future native llmtool executor remain independent gates.
