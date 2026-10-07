# PR #46 — A11b.2.2.0 independent review and bounded repair

## Current task

A11b.2.2.0 has already been implemented on PR #46. Do not repeat its implementation
or create another feature PR. David authorized updating the handoff on 2026-10-07
after confirming this published implementation.

- Repository: davidcatcat/davidcattoolsproject.
- Main/base: `18365bb079835f3b413dd135ea742213bb1a7724` (PR #44 merged).
- Existing PR: https://github.com/davidcatcat/davidcattoolsproject/pull/46.
- Branch: `feat/llmtool-a11b2-2-0-cleanup-20261007`.
- Starting publication HEAD: `687f3f93d6d46b6d4fb5a95a7cfd1fb17170f53c`.
- Existing implementation SHA: `7692fa3fb49b45b49b38cc1424eaa9811fe4763f`.
- Existing report: reports/llmtool_a11b2_2_0_cleanup_validation_20261007.md.
- Push and PR llmtool CI at the starting publication HEAD passed when this task
  was prepared. New publication requires checks on its own exact HEAD.

The script checks the actual open/unmerged PR state, main/base, branch identity,
and exact starting HEAD before spending model tokens. A changed PR/base requires
a refreshed contract. Reuse/fast-forward the existing local branch safely; never
reset, stash, force-push or discard unrelated work.

## Run in this order

1. Select local test coverage from task.json. Default `--related` runs syntax,
   Git-preflight, Git-intelligence/A5 and Action-contract tests using the existing
   `tools/llmtool/.venv/bin/python`. Explicit `--full` runs the canonical checks
   with both that interpreter and Mac `/usr/bin/python3`. Run from the canonical
   repository, with `PYTHONPATH=tools/llmtool` for root-based unittest discovery.
   Do not assume a root `.venv` or silently substitute a different interpreter.
   Related mode requires the full canonical suite in final exact-head CI; it does
   not claim a local full-suite PASS. This selection policy was requested by David
   on 2026-10-07 to avoid full local checks in every repair round.
2. Claude independently reviews the full cumulative base-to-current-HEAD diff,
   originating L1/L2 requirements, and current deterministic test evidence. It
   has read tools only. It does not modify files, run tests, write Git or merge.
3. If tests and review PASS, skip Codex entirely. Preserve the existing code and
   validation report; publish the new independent handoff report only.
4. On a reproducible test failure or blocking finding, Codex may repair within
   the three allowed_files in task.json. Retain the actual error/test evidence.
   Update the validation report only to add truthful repair/verification evidence;
   preserve historical before-fix evidence and do not present old test results as
   validation of changed bytes. Related tests run after each repair, the runner
   commits, and Claude re-reviews the full cumulative diff. In full mode, run the
   final local full suite after review PASS if the tested bytes changed; reuse an
   initial full PASS only when the same bytes remain. A final full failure returns
   to bounded repair. At most two repairs in total, including such failures.
5. Publish the dated automated review report and any tested repairs to the same
   PR #46 branch. Wait for applicable llmtool CI runs on the exact final publication
   HEAD. Report-only pushes may omit a push workflow due to path filters, so accept
   the applicable PR workflow at that exact HEAD; missing/old checks are not PASS.
   CI runs the full canonical suite on Linux/Python 3.9 and 3.14; this does not
   substitute for Mac-specific evidence. Record local mode/results separately
   from CI. Preserve the existing PR description and append this new handoff.
6. Stop at READY_FOR_FINAL_REVIEW. Final coordinator/merge decisions remain pending.

## Review requirements

Read AGENTS.md, CLAUDE.md, tools/llmtool/WORKFLOW.md,
skills/review-change/SKILL.md, and (for repairs) skills/implement-feature/SKILL.md.
Review the original requirements in:

- reports/llmtool_a11b2_1_git_preflight_independent_review_20261007.md.
- reports/llmtool_a11b2_2_b5_containment_failure_spec_20261007.md, especially §18/§19.
- reports/llmtool_a11b2_2_b5_containment_spec_targeted_rereview_20261007.md.

Check that PermissionError/ProcessLookupError cleanup does not replace the primary
typed failure, resource-release failures cannot skip later pipe closure/reaping,
and cleanup faults on otherwise successful reads cannot silently report success.
Check single-variable config/info-attributes/index/root drift evidence and honest
labels for compound probes. A real correctness or regression blocker needs concrete
file/test evidence; optional naming/refactoring suggestions do not force repairs.

Ordinary review does not claim B5 crash containment, formal security/QA approval,
or Mac parent-SIGKILL/supervisor evidence. Return NEEDS_DECISION for unresolved
architecture, scope or authorization conflicts. Structured outputs and exact
reviewed_head are required; CLI exit zero alone is not review PASS.

## Boundaries

No reimplementation from main, duplicate PR, automatic merge, new milestone,
supervisor/fake-child harness, real llmtool Git ADD, public mutation CLI, approval
enforcement, rollback/index.lock deletion, A11b.2.2.1/.2/.3, A11c or A12. Keep
roadmap/README/skills unchanged. The outer script owns Git; Codex must not stage,
commit or push. Ordinary development publication does not enable product mutation.

Auth/quota/CLI errors, dirty or divergent local work, stale PR/main, invalid model
output, or exhausted repair bounds stop with files/logs preserved. No blind provider
retry or permission bypass. This is a Mac script run, not an automatically connected
Work execution or approval/resume service.
