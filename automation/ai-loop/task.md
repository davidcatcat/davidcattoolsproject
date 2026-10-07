# A11b.2.2.0 — prerequisite cleanup and drift evidence

This is the next bounded implementation task selected by David's 2026-10-07
instruction to prepare the next task and automate Codex/Claude handoff. Executing
the launcher starts this task only. It does not authorize later slices or merge.

## Verified prerequisite

PR #44 is merged. The pinned main/base is
`18365bb079835f3b413dd135ea742213bb1a7724`.
The B5 targeted re-review was published before that merge. The launcher must fail
closed if main advances before this contract is updated.

Read AGENTS.md, tools/llmtool/WORKFLOW.md, skills/implement-feature/SKILL.md,
reports/llmtool_a11b2_1_git_preflight_independent_review_20261007.md,
reports/llmtool_a11b2_2_b5_containment_failure_spec_20261007.md (especially §18/§19),
and reports/llmtool_a11b2_2_b5_containment_spec_targeted_rereview_20261007.md.
Historical "documentation-only authorization" in those reports describes those
past tasks. This task permits the prerequisite 2.2.0 changes listed below only.

## Implement

1. Inspect the existing read-only Git ADD preflight runner in
   tools/llmtool/llmtool/git_mutation.py. Fix A11b.2.1 L1 cleanup so both
   PermissionError and ProcessLookupError in process-group cleanup are handled.
   Preserve the original typed failure. Use nested finally blocks so every opened
   pipe closes even when cleanup fails. Do not change failure/CLI contracts or
   turn an inconclusive cleanup into proof of containment.
2. Add regression evidence for PermissionError/EPERM, ProcessLookupError,
   original typed failure preservation, and deterministic pipe closure. Keep
   tests tied to observable failure behavior; do not weaken existing tests.
3. Split/rename L2 drift tests in tools/llmtool/tests/test_git_mutation.py so
   config, info/attributes, index, and root drift each have an independent
   single-variable test/reproducer. Keep original rejection behavior and evidence
   coverage. Inspect the mixed
   test_mid_query_instruction_file_index_root_and_config_drift case first.
4. Run the targeted tests and canonical make check through the existing canonical
   .venv. Record exact commands, exit status, environment and tested bytes/commit
   provenance in reports/llmtool_a11b2_2_0_cleanup_drift_validation_20261007.md.
   The outer runner repeats these tests and records the committed implementation
   SHA. Do not invent Mac signal/runtime results not actually exercised.

## Scope and stopping point

Only the three allowed_files in task.json may be edited by Codex. Keep roadmap,
README and skills unchanged for this small code prerequisite; final state updates
remain a separate coordinator decision after review/merge.

No supervisor implementation, fake-child containment harness, real Git ADD
executor, public mutation CLI, approval enforcement, rollback, index.lock
deletion, A11b.2.2.1/.2/.3, A11c or A12. Git staging/commit/push performed by the
outer development script is ordinary repository publication; it does not enable
llmtool Git mutation.

Codex edits source/tests and returns a structured implementation result. It must
not stage, commit, push or merge. The script is the sole Git writer and publishes
only this feature branch. Claude performs ordinary read-only review of the full
cumulative diff and deterministic test evidence. It does not silently satisfy a
separately required security/QA/containment gate.

## Acceptance

- New regression tests demonstrate the cleanup fixes and independently test each
  drift category. Existing canonical checks pass without weaker expectations.
- Independent Claude review has no unresolved blocking findings.
- Script verifies final publication HEAD's llmtool CI, publishes a dated report
  and opens a PR. No automatic merge or next milestone.
- A clear bug/test failure may be repaired automatically up to two times. An
  architecture/scope/authorization conflict, malformed output, auth/quota error,
  unchanged implementation, stale main or unresolved blocker stops with preserved
  local evidence. The scripts do not retry failed providers blindly.
