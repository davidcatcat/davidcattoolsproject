# #1874 follow-up contract — review-side facts (slice 1)

#2367 P1-4, slice 1. **Design and acceptance work only: no P1 code is
written here.** This note records, from code, the **review-side** facts only
(`tools/llmtool/llmtool/review_dispatch.py` and
`tools/llmtool/llmtool/task_submission_cli.py` on this head). Everything about
the **repair side** is explicitly marked **UNKNOWN (slice 2)**. No code or test
is changed; this file is the only write.

## A1 — How the review dispatch picks the Task whose contract the Reviewer gets for a PR head

The review-side dispatch entry point is `dispatch_review` in
`tools/llmtool/llmtool/review_dispatch.py`, which delegates prompt rendering to
`run_task_review_prompt` in `tools/llmtool/llmtool/task_submission_cli.py`.

### The dispatched Task binding is caller-driven

- `dispatch_review` (`tools/llmtool/llmtool/review_dispatch.py`) takes
  `task_id` as an explicit parameter and loads that single Task via
  `store.get(task_id)`. There is **no code in `review_dispatch.py` that
  enumerates sibling Tasks sharing a D6 or selects a "newest" or "widest"
  Task** — the bound `task_id` is entirely determined by the caller.
- In `_preflight` (`tools/llmtool/llmtool/review_dispatch.py`), that Task's
  `task_binding` is validated by `task_completion.validate_task_binding`
  (with `task_id`, `run_id`, and `contract_digest`). A missing or inconsistent
  binding is refused (`REVIEW_PROMPT_BINDING_MISSING`).
- `dispatch_review` and `_verdict_binding_line`
  (`tools/llmtool/llmtool/review_dispatch.py`) bind the review claim and the
  verdict binding line to **that single Task**:
  `LLMTOOL-REVIEW-BINDING v1 task=<task_id> run=<run_id> contract=<contract_digest> head=<head>`.

### What Task contracts and prompt contents the Reviewer actually receives

When `dispatch_review` (`tools/llmtool/llmtool/review_dispatch.py`) builds the
prompt for either `codex-reviewer` or `claude-reviewer`:

1. **Rules prefix (`_review_rules`):** `dispatch_review` calls
   `_review_rules` (`tools/llmtool/llmtool/review_dispatch.py`), which reads
   role cards from the authenticated `main` commit and wraps them with
   `_REVIEW_RULES_HEADER` and `_REVIEW_RULES_FOOTER`. `dispatch_review`
   prepends this `rules_prefix` verbatim to the rendered prompt.
2. **PR and D6 scope (`run_task_review_prompt`):** `run_task_review_prompt`
   (`tools/llmtool/llmtool/task_submission_cli.py`) verifies `task_binding`,
   the executor D6 registration (`result_sha`, snapshot, and
   `result_head_admitted`), the reviewer D6 registration, and `pulls/{pr}`
   (`pr['head']['sha'] == expected_head`, plus `base_sha`). It emits:
   - `Review PR #<pr> at exact HEAD <expected_head>.`
   - `Scope: git diff <base_sha>...<expected_head>`
   - Read-only / no-mutation instructions, plus role-specific instructions for
     `Codex` (no local tests, do not post to GitHub) or `Claude` (run only
     relevant tests, post verdict and end session).
3. **Current Task contract (`run_task_review_prompt`):** `run_task_review_prompt`
   validates `task.get('pm_assignment')` via its inner `checked_contract`
   helper (requiring `schema_version == 1`, `route`, `risk`, `goal`, and 1–16
   `acceptance` items, all screened by `_screen_text`) and emits:
   - `<<< BEGIN PM TASK CONTRACT DATA >>>`
   - `Task goal: <pm_assignment['goal']>`
   - Numbered acceptance items `1. ...` from `pm_assignment['acceptance']`
   - (For `Claude`, `Verdict FAIL if any acceptance item is unmet; a missing required test is unmet.`
     inside the block)
   - `<<< END PM TASK CONTRACT DATA >>>`
4. **Earliest sibling Task contract on the same D6 (`_review_original_task`):**
   Unlike `review_dispatch.py`, `run_task_review_prompt` **does** inspect
   sibling Tasks on the same D6 via `_review_original_task`
   (`tools/llmtool/llmtool/task_submission_cli.py`). `_review_original_task`
   scans `store.completion_d6_task_ids(executor_task)` (or `store.task_ids()`
   if `completion_d6_task_ids` is absent) for non-abandoned other Tasks whose
   `completion.executor_task_id == executor_task` and
   `created_at < task['created_at']`, and picks the **earliest** one
   (`min` by `(created_at, task_id)`). If found, `run_task_review_prompt`
   validates the original Task's `pm_assignment` with `checked_contract` and
   appends:
   - `<<< BEGIN ORIGINAL TASK CONTRACT DATA >>>`
   - `Original Task id`, `Original Task goal`, and numbered original
     `acceptance` items
   - `<<< END ORIGINAL TASK CONTRACT DATA >>>`
   - `Repair delta: current acceptance items not in the original contract: <indices or none>.`
   - `Judge the PR against both contracts; where a current item replaces an original one, the current item governs; an original item not replaced still applies.`
5. **Test evidence and verdict format (`run_task_review_prompt`):**
   - For `Codex`, `run_task_review_prompt` appends `_review_ec2_line(task, expected_head)`
     (the EC2 or GitHub-hosted `remote-test` receipt summary on `task` for
     `expected_head`, or `_REVIEW_EC2_NONE`) and `*_review_ci_lines(expected_head)`
     (latest status/conclusion for `check (Linux 3.14)`, `check (macOS 3.13)`,
     and `check (docs)`), followed by finding-format rules, PASS/non-PASS
     header/marker templates (including `binding_line` for the single bound
     `task_id`), and JSON output instructions.
   - For `Claude`, `run_task_review_prompt` appends the exact PASS/non-PASS
     verdict marker, header, `task_completion._FORMAL_ACCEPT`, and
     `binding_line` to post.
6. **Write scope (`_create_d6` and `run_task_review_prompt`):**
   - `_create_d6` (`tools/llmtool/llmtool/review_dispatch.py`) creates the
     Reviewer D6 as a **read-only** worktree at `base_sha=head` with lease
     `"read-only formal review of executor task <executor_task> at <base_sha>"`.
   - `pm_assignment` (validated by `checked_contract` in
     `run_task_review_prompt`) carries `goal` and `acceptance`, not
     `developer.write_paths`; the Reviewer prompt scopes the code under review
     to `git diff <base_sha>...<expected_head>`.

### Where the head (and hence the PR diff) comes from

- In `_preflight` (`tools/llmtool/llmtool/review_dispatch.py`) and
  `run_task_review_prompt` (`tools/llmtool/llmtool/task_submission_cli.py`),
  the executor D6 registration is read via `task_completion.read_d6_registration`
  using `completion['evidence']['common_dir']` and `executor_task`.
- `head = executor.result_sha` is checked against a clean git snapshot
  (`task_completion.collect_git_snapshot`) and against the recorded result
  (`result_head_admitted` in `tools/llmtool/llmtool/task_submission_cli.py`).
- Both `_preflight` and `run_task_review_prompt` fetch `pulls/{pr_number}` and
  refuse (`REVIEW_PROMPT_PR_HEAD_MISMATCH`) unless `pull['head']['sha'] == head`.
  The PR `base['sha']` is used by `run_task_review_prompt` for
  `Scope: git diff <base_sha>...<expected_head>`.

## What happens when several Tasks share the D6

- A follow-up Task records `completion.executor_task_id` and
  `completion.evidence.common_dir` pointing at the **same** executor D6, whose
  `result_sha` equals the PR head (`pulls/{pr}.head.sha`) reflecting the
  **accumulated** changes on that D6.
- `dispatch_review` (`tools/llmtool/llmtool/review_dispatch.py`) still binds
  the claim, `contract_digest`, and `_verdict_binding_line` to **only** the
  single caller-supplied `task_id`.
- `run_task_review_prompt` (`tools/llmtool/llmtool/task_submission_cli.py`)
  renders `Scope: git diff <base_sha>...<expected_head>` (the full PR diff),
  the bound `task_id`'s `pm_assignment`, and — when `_review_original_task`
  finds an earlier non-abandoned Task on the same `executor_task` — that
  **single earliest** Task's `pm_assignment` plus the repair delta.
- Consequently, when more than two Tasks share a D6, intermediate Tasks'
  contracts are not included in the prompt; if `_review_original_task` finds
  no earlier non-abandoned Task on the D6 (for example, if the earlier Task
  was abandoned or `store.task_ids` is absent), only the dispatched `task_id`'s
  `pm_assignment` is included. In all cases, `write_paths` is absent from the
  prompt contract data, `_review_ec2_line` checks receipts on the single
  dispatched `task`, and `LLMTOOL-REVIEW-BINDING` binds only that single
  `task_id`.

## A2 — Which #1874 symptom this explains

**Symptom #1874** (the review scope is **narrower than the PR diff** on PR
#1866 and PR #1863) is explained by the review-side facts above:

- The PR head (`pulls/{pr}.head.sha == executor.result_sha`) and the prompt's
  `Scope: git diff <base_sha>...<expected_head>` span the **accumulated**
  work of all Tasks on the shared D6.
- Meanwhile, `dispatch_review`, `_preflight`, and `_verdict_binding_line`
  (`tools/llmtool/llmtool/review_dispatch.py`) bind the review claim and
  `contract_digest` to the **single caller-supplied `task_id`**.
- On this head, `run_task_review_prompt` and `_review_original_task`
  (`tools/llmtool/llmtool/task_submission_cli.py`) partially widen the prompt
  text by appending the **earliest** non-abandoned Task's `pm_assignment` when
  one exists on the same D6, but intermediate Tasks' contracts are still not
  included, `write_paths` is not part of `pm_assignment`, and the binding
  (`task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, and
  `_review_ec2_line`) remains tied to the single dispatched `task_id`.

This note **reports the review-side facts only**. It does not propose or
implement a fix.

## UNKNOWN (slice 2 — repair side)

The following are **not** determinable from the read set
(`tools/llmtool/llmtool/review_dispatch.py` and
`tools/llmtool/llmtool/task_submission_cli.py`) or belong to the later repair
design, and are marked **UNKNOWN** for slice 2:

- **UNKNOWN:** which caller / CLI layer chooses the `task_id` handed to
  `dispatch_review`, and whether that caller implements the "newest Task on
  the D6" selection observed in #1874.
- **UNKNOWN:** whether a "newest-Task" caller selection is *intended* or is
  itself part of the defect to be repaired.
- **UNKNOWN:** whether a Task's `task_binding.contract_digest` and
  `LLMTOOL-REVIEW-BINDING` are intended to stay bound to one Task or be
  widened/unioned across all Tasks on the D6 (including intermediate Tasks
  between the earliest and newest).
- **UNKNOWN:** the repair mechanism itself (slice 2).
