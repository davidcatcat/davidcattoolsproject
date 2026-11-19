# Merge-gate gaps from #1132: current behaviour on main

Read-only note. Evidence is a read of `tools/llmtool/llmtool/task_advance.py` at
source `47fc6aa98ee79a9d8d9654c4efc7308cf4b953b3`. Unless another file is
named, function names below are in that file. No code or test change is part
of this note.

## Items checked

The #1132 items, quoted without their old line references:

1. "a newer RUNNING, UNKNOWN or DISPATCHED review claim with no event does not
   block an older PASS"
2. "closing through integration_pr does not check the delta (a clean-merge
   check from #1129 could be reused)"
3. "TUI PRs have no merge route through task advance --merge"
4. "scripts/ PRs can never pass because the merge gate needs a Linux job that
   the Linux CI workflow does not schedule for them"

Item 5 is not restated here. It is covered by the #1874 follow-up contract
note (PR #2380).

## Status summary

| Item | Status |
| --- | --- |
| 1 | CLOSED |
| 2 | CLOSED |
| 3 | CLOSED |
| 4 | UNKNOWN |

## Item 1: newer review claim with no event

**Status: CLOSED.**

`_merge_pass_claim` in `tools/llmtool/llmtool/task_advance.py` first finds the
highest-version claim that has a PASS verdict and a matching REVIEWER
`REVIEW_PASS` event. It then scans every claim for the same PR and head and
returns `MERGE_NEWER_REVIEW_EXISTS` for any claim with a higher integer
version. That scan does not require the newer claim to have a REVIEWER event
or a terminal state, so RUNNING, UNKNOWN and DISPATCHED claims all veto the
older PASS; a claim with no integer version also vetoes it. A newer REVIEWER
event at the same head also vetoes it.

`_merged_review_target` applies the same newest-claim rule for integer
claim versions before it will report a Task closed on a merged PR or
integration PR. A newer claim without a PASS makes it return no merge target,
so the Task does not close.

## Item 2: delta check when closing through integration_pr

**Status: CLOSED.**

In `_merged_review_target`, a direct merged PR is accepted only when the PR
head is the reviewed head. For an integration PR, the function reads
`compare/<reviewed_head>...<integration_head>`, requires status `ahead` or
`identical`, and then calls `_integration_delta_verified`.

`_integration_delta_verified` checks the delta rather than trusting the
integration PR: it anchors on the first parent of the integration PR's merge
commit, rejects legacy graft files through `_reject_legacy_grafts` in
`tools/llmtool/llmtool/task_completion.py`, and requires `_developer_tip` in
that module to return the reviewed head. In other words, the integration head
may add only clean merges of main after the reviewed head.

`_advance_step` repeats the same check in its `READY_FOR_INTEGRATION` path.
When the delta is not verified it emits `INTEGRATION_DELTA_UNVERIFIED` and
does not close the Task; the step stays `READY_FOR_INTEGRATION`. The
`WAITING_INTEGRATION` code is added only in the separate else branch, when the
merged result is not issued (not merged, compare not `ahead` or `identical`,
or no valid merge commit SHA); that branch also leaves the step at
`READY_FOR_INTEGRATION`.

## Item 3: TUI PRs and the merge route

**Status: CLOSED.**

TUI handling is concentrated in `_owner_acceptance_required`, which
`_merge_checks` uses when no Owner acceptance has already been supplied. TUI
test files alone (`tools/llmtool/tests/test_chat_tui*` and
`tools/llmtool/tests/test_tui_*`) no longer require Owner acceptance, so such
a PR reaches the normal exact-head CI and mergeability gates and can merge
through `task advance --merge`.

Product TUI code (`tools/llmtool/llmtool/chat_tui*.py`,
`tools/llmtool/llmtool/tui_*.py`) and the TUI PTY driver
(`tools/llmtool/tests/tui_pty_driver.py`) still require Owner acceptance by
design (Owner O3, #1943). Without it, `_merge_checks` returns
`MERGE_OWNER_ACCEPTANCE_MANUAL`. That is a deliberate Owner-acceptance route,
not a missing route: `advance` accepts `owner_acceptance` only together with
`merge`, and `_merge_once` passes it to `_merge_checks` and
`_owner_acceptance_code`.

## Item 4: scripts/ PRs and the Linux job

**Status: UNKNOWN.**

On the code side, `_merge_checks` in
`tools/llmtool/llmtool/task_advance.py` requires the exact-head Linux checks
returned by `_required_linux_checks`. That function loads `linux_flags` from
`tools/llmtool/scripts/ci_path_classes.py` through `_ci_path_classes`,
classifies the changed and renamed paths, and returns `None` (fail closed)
when the classifier cannot be read, the flag tuple is malformed, or the
classifier enables no Linux flag. `_review_ci_gate` applies the same
requirement before review. There is no `scripts/` special case in
`tools/llmtool/llmtool/task_advance.py`.

Whether the Linux CI workflow schedules `check (Linux 3.14)` for `scripts/`
paths, and therefore whether a `scripts/` PR can reach a green required set,
is workflow content outside this scope. This note cannot mark the gap closed
or open.

If the workflow side is later confirmed to omit the job, the fix Task would
need to change `_required_linux_checks` and the matching gate use in
`_merge_checks` in `tools/llmtool/llmtool/task_advance.py`, together with the
Linux workflow and the shared path classifier. No fix Task is specified by
this note while the item remains UNKNOWN.

## Fix-task need

Items 1-3 are closed on the code read here. Item 4 is UNKNOWN, so no fix Task
can be justified from this scope. No code, test or workflow file is changed by
this note.
