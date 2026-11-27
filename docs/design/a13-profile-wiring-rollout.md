# A13.0 profile wiring — rollout runbook

Status: draft. Some wiring PRs are still pending; this runbook is usable
while they are.

Basis
- Issue #2793, Coordinator review comment 7210501920: adopt the profile
  wiring in dependency-ordered stages; nothing ships on a partial
  profile.
- A13.0 Roadmap migration and rollback exit requirement: the migration
  is complete only when every slice passes its checks and the rollback
  path has been verified end to end.

Out of scope for this runbook: changing code, workflow, or live state;
weakening the existing remote EC2 evidence or any gate; repeating the
separate test-mode interface design (that design doc applies instead).

Profile source: `tools/llmtool/llmtool/ci_profile.py` — `CiProfile`
(immutable value), `default_profile()` (current check and workflow file
names), `CiProfileError` (fail-closed name validation).

## 1. Staged rollout checklist (PM)

Work the stages in order. Any failed step stops the rollout and starts
§2.

Stage 0 — record
1. Record the `main` HEAD.
2. Record the controller revision in use.
3. Record the HEAD of every reviewed wiring PR, one row per slice in §3.

Stage 1 — verify each slice on its recorded HEAD, dependency order
4. Default parity: at the real entry points the default profile yields
   exactly the check set from `default_profile()`.
5. Non-default fixture use: the real entry points accept and honour a
   non-default profile; no hard-coded names at the entry points.
6. Fail-closed validation: an invalid profile name is rejected with
   `CiProfileError` at construction, never silently coerced.
7. EC2 evidence: the existing remote EC2 run reports the expected
   checks; record the output in the slice row in §3.
8. Required CI: every check in the profile passes — Linux code check,
   docs check, gateway check, ai-loop check, macOS check.
9. Independent review: signed off by a reviewer outside the slice's
   author set. Gates held: exact-head hosted docs check; formal review
   by codex-reviewer.

Stage 2 — sync
10. Merge one slice at a time in dependency order, each on its recorded
    HEAD; no rebases or re-recording after Stage 0.
11. After sync, confirm the live checkout uses the intended revision:
    `main` HEAD and controller revision must match the Stage 0 values.

## 2. Rollback

Triggers (any one starts the rollback path):
- A required CI check fails on a recorded slice HEAD.
- EC2 evidence diverges from the recorded expectation.
- The live checkout or controller is at an unexpected revision after
  sync.
- Independent review rejects a slice, or the exact-head docs check
  fails.
- A gate is weakened or bypassed to make a stage pass.

Safe rollback checklist — no force-push, no manual TaskStore edits:
1. Stop advancement on the affected slice and every dependent slice.
2. Retain the Task, its §3 evidence row, logs, and EC2 output.
3. Identify the affected slice and its dependents from the §3 table.
4. Restore via a reviewed revert in dependency-safe order (dependents
   first); each revert carries its own review and required CI.
5. Rerun the relevant Stage 1 checks for the affected slices.
6. Verify the controller revision and `main` HEAD against the recorded
   pre-sync values; resume only when both match and the affected checks
   are green.

The rollback drill and live verification have not been run as of this
runbook; both are pending and are required by the roadmap exit
requirement.

## 3. Per-slice evidence checklist

`head` = recorded HEAD; `verdict` = ok only when steps 4–9 all pass for
that HEAD.

| slice | feature | head | verdict | tests (evidence) | rollback target |
|-------|---------|------|---------|------------------|-----------------|
| S1 | profile value + fail-closed validation (`ci_profile.py`) | pending | pending | construction and parity tests (pending) | pre-sync `main` |
| S2 | real entry points wired to the profile | pending | pending | default parity + non-default fixture; EC2 evidence (pending) | S1 |
| S3 | required CI names and coverage | pending | pending | full CI run on the recorded HEAD (pending) | S2 |

Rollback target = the previously recorded HEAD the slice reverts to,
never a hand-edited state.
