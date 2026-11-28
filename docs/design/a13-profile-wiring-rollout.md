# A13.0 profile wiring — rollout runbook

Status: draft; usable while wiring PRs are still pending.

Basis
- Issue #2793, Coordinator review comment 7210501920: adopt the profile
  wiring in dependency-ordered stages; nothing ships on a partial
  profile.
- A13.0 Roadmap migration and rollback exit requirement: migration
  complete only when every slice passes its checks and the
  migration/rollback and regression tests pass. An end-to-end rollback
  drill is not a Roadmap requirement; the §2 drill is optional.

Out of scope: changing code, workflow, or live state;
weakening the existing remote EC2 evidence or any gate; repeating the
separate test-mode interface design.

Profile source: `tools/llmtool/llmtool/ci_profile.py` — `CiProfile`,
`default_profile()`, `CiProfileError`.

## 1. Staged rollout checklist (PM)

Work the stages in order. Any failed step stops the rollout and starts
§2.

Stage 0 — record (before any merge)
1. Record the `main` HEAD (pre-sync baseline).
2. Record the controller revision in use.
3. Record the HEAD of every reviewed wiring PR, one row per slice in §3
   (the post-sync `main` revision is recorded in Stage 2 as it
   happens).

Stage 1 — verify each slice on its recorded HEAD, dependency order
4. Default parity: at the real entry points the default profile yields
   exactly the check set from `default_profile()`.
5. Non-default path use: the non-default profile's CI check names and
   workflow file names reach their real consumers — real test entry
   points accept and honour the profile (test-profile consumers,
   non-default test-path fixture) and CI/workflow consumers use the
   non-default check and workflow file names; no hard-coded names.
6. Refusal evidence: for missing, invalid, or ambiguous profile
   configuration the real entry points refuse (fail-closed); an invalid
   name is rejected with `CiProfileError` at construction, never
   silently coerced or defaulted.
7. EC2 evidence: the existing remote EC2 run reports the expected
   checks; record the output in the slice row in §3.
8. Independent review: signed off by a reviewer outside the slice's
   author set. Gates held: exact-head hosted docs check; formal review
   by codex-reviewer.
9. Required CI: on the recorded HEAD, the Linux code check, docs
   check, gateway check, and ai-loop check all pass.
10. macOS check: formal review must be PASS before the PM enables the
    macOS check (mac-ci); once enabled, require its exact-head success
    on the recorded HEAD before merge.

Stage 2 — sync
11. Merge one slice at a time in dependency order; each slice merges
    at the HEAD recorded in §3 (only the §2 pre-merge path may
    re-record it). Before
    each merge, record the then-current `main` HEAD in that slice's
    rollback target column in §3; after each merge, record the actual
    new `main` HEAD.
12. After the last merge, confirm the live checkout uses the intended
    revision: the live `main` revision equals the final recorded
    post-merge `main` HEAD and the controller reports the revision from
    step 2 — never the step 1 baseline, which `main` has deliberately
    passed.

## 2. Rollback

Triggers (any one starts the rollback path):
- A required CI check fails on a recorded slice HEAD.
- EC2 evidence diverges from the recorded expectation.
- The live checkout or controller reports an unexpected revision after
  sync or after a revert.
- Independent review rejects a slice, or the exact-head docs check
  fails.
- A gate is weakened or bypassed to make a stage pass.

Safe rollback checklist — no force-push, no manual TaskStore edits:
1. Stop advancement on the affected slice and every dependent slice.
2. Retain the Task, its §3 evidence row, logs, and EC2 output.
3. Identify the affected slice and its dependents from the §3 table.
4. Pre-merge path: no merge has landed, so there is nothing to revert.
   Fix the slice, re-record its HEAD in §3, and rerun its Stage 1
   checks; no revert is issued.
5. Post-merge path: restore via a reviewed revert in dependency-safe
   order (dependents first); each revert carries its own review and
   required CI.
6. Rerun the relevant Stage 1 checks for the affected slices.
7. Verify the rollback: each affected slice's content equals the §3
   rollback target recorded before its merge, and the controller
   reports the revision recorded in step 2. A reviewed revert makes a
   new `main` commit, so compare against those explicit targets, not
   the step 1 baseline. Resume only when both hold and the affected
   checks are green.

The rollback drill (not a Roadmap requirement), the regression tests,
and live verification are all pending; none has been run.

## 3. Per-slice evidence checklist

`head` = recorded slice HEAD; `verdict` = ok only when steps 4–10 all
pass for that HEAD. Rollback target = the `main` HEAD recorded before
that slice's merge (step 11) — always a recorded HEAD, never a
hand-edited state.

| slice | feature | head | verdict | tests (evidence) | rollback target |
|-------|---------|------|---------|------------------|-----------------|
| S1 | profile value + fail-closed validation | pending | pending | construction and refusal (missing/invalid/ambiguous) tests (pending) | `main` HEAD before S1 merge (pending) |
| S2 | real test entry points wired to the profile | pending | pending | default parity, non-default test-path fixture + CI/workflow names at real consumers, EC2 evidence (pending) | `main` HEAD before S2 merge (pending) |
| S3 | required CI names and coverage | pending | pending | full CI run on the recorded HEAD (pending) | `main` HEAD before S3 merge (pending) |

The rollback target column is filled in during Stage 2; rows stay
pending until every cell is recorded or checked.
