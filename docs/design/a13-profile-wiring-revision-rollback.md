# A13.0 profile wiring: rollout and rollback checklist

Date: 2026-11-28

Documentation only. No drill, run, live verification or exit closure is claimed. No code, workflow, gate or live state changes.

## Scope and actors

A slice is one reviewed PR merging one step of the A13.0 profile wiring. Slices merge one at a time in the approved order. The release operator records the ledger; the PM merges through normal `llmtool task advance`. No other writer touches the checkout.

Do not pre-fill ledger values. Record each value at the named step when it exists. Number slices S1, S2, ... only when they reach R1.

## Revision ledger

Record these in the Task record for each slice i:

| Name | Recorded at | Meaning |
| --- | --- | --- |
| `PRE_i` | R1 | origin/main SHA immediately before slice i merges; the recorded pre-slice baseline and rollback target for slice i |
| `HEAD_i` | R2 | slice i PR head SHA that review and checks bind to |
| `MERGE_i` | R4 | merge commit SHA on main after slice i merges |
| `LIVE_k` | L1 | freshly fetched origin/main SHA for each live check |
| `REVERT_i` | F2 | head SHA of the reviewed revert PR for slice i |
| `POST_REVERT_i` | F2 | merge commit SHA on main after the revert PR merges |

`PRE_i` and `MERGE_i` are immutable historical revisions and may be followed by later main commits. They are never live-check values. Record `LIVE_k` fresh every time; never reuse an earlier one. Do not assume `PRE_{i+1}` equals `MERGE_i`, because other PRs may merge in between.

## Preflight, before each attempt

- P1. Confirm the checkout is the registered D6, has one writer, and `git status --porcelain` is empty.
- P2. Run `git fetch origin main`; record `LIVE_k` from `git rev-parse refs/remotes/origin/main`. Without a pin, a checkout behind origin/main is not live. Do not continue until the checkout is on the live revision described in L1.
- P3. Confirm the slice PR, its order, and its exact head `HEAD_i`. A new push that changes `HEAD_i` requires review again.

## Rollout, one slice at a time

- R1. Repeat P1-P2. Record `PRE_i` as the current origin/main SHA. This is the concrete rollback target for slice i.
- R2. Record `HEAD_i` and confirm the required exact-head checks and formal review bind to it.
- R3. Merge only slice i. Do not merge the next slice in the same step.
- R4. Run `git fetch origin main`; record `MERGE_i` from `git rev-parse refs/remotes/origin/main`. Confirm `git merge-base --is-ancestor HEAD_i MERGE_i` succeeds, so the reviewed revision is the merged one.
- R5. Run L1. If it fails, go to F2. If it passes, stop and wait for the next slice, which starts again at R1.

## L1: live checkout check, after every merge or revert

These checks use `read_pin()` and `source_freshness()` in `tools/llmtool/llmtool/source_freshness.py`.

- L1.1. Run `git fetch origin main`; record the current origin/main SHA as `LIVE_k`.
- L1.2. Require `git status --porcelain` empty. Without an approved install pin (`read_pin()` returns None), require HEAD to equal `LIVE_k`. With a pin, require HEAD to equal the revision this step intends to be live (`MERGE_i` after a merge, `POST_REVERT_i` after a revert), and require that revision to equal `approved_sha` from `read_pin()`. If the pin names an older revision, advance it through the approved-install procedure first; stop if unavailable. Do not hand-edit or delete the pin, or force HEAD. Do not require HEAD to equal `LIVE_k` with a pin.
- L1.3. If the check fails, stop and repair. `source_freshness()` returns `STALE` for a checkout behind origin/main with no pin. With a pin it returns `OK` only at `approved_sha`, `UNAPPROVED` when `approved_sha` is an ancestor of HEAD, and `STALE` when HEAD is behind or diverged. With no pin, behind origin/main is never live; with a pin, `approved_sha` is live even if main moved ahead, but behind `approved_sha` is not. Never compare HEAD to `PRE_i` or an older `MERGE_i`.

## Failure paths

### F1: before a slice merges

This covers a failure before any merge, or of an unmerged slice after earlier slices merged. Do not revert: nothing is merged for the failing slice, and do not revert an earlier slice for this failure. Stop, keep main untouched, and fix the slice PR or open a replacement PR under the normal task flow. Restart at P1 with a new `HEAD_i`; if main moved, record a new `PRE_i` at R1. If abandoned, withdraw the PR.

### F2: after a slice merges

Revert only a merged slice, in reverse merge order (last merged first). Open a separately reviewed revert PR that reverts `MERGE_i` (`git revert -m 1 MERGE_i` when it is a merge commit, otherwise `git revert MERGE_i`). Record `REVERT_i`, obtain formal review and exact-head checks, merge it, record `POST_REVERT_i`, then run L1. The rollback target is `PRE_i` (R1); the reverted commit is `MERGE_i` (R4). If unrelated commits landed after `MERGE_i`, the revert removes only slice i; do not require tree equality with `PRE_i`.

- Use no force operation: no force push, reset, rebase of main, or direct push to main.
- Do not edit the TaskStore by hand; use the normal task flow.
- If the revert conflicts or L1 still fails, stop and escalate. Do not force or hand-edit around the failure. Repair a failed revert with a new reviewed PR.

## Completion

The rollout ends only when the last slice passes R5/L1. The PM records completion through the normal task flow. This document does not itself close an exit or report a drill or live result.
