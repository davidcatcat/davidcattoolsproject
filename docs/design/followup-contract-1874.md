# #1874 follow-up contract — review-side facts (slice 1)

#2367 P1-4, slice 1. **Design and acceptance work only: no P1 code is
written here.** This note records, from code, the **review-side** facts only.
Everything about the **repair side** is explicitly marked **UNKNOWN (slice
2)**. No code or test is changed; this file is the only write.

## A1 — How the review dispatch picks the Task whose contract the Reviewer
gets for a PR head

The review-side dispatch is
`dispatch_review` in `tools/llmtool/llmtool/review_dispatch.py`.

### The binding is caller-driven; the code does not scan for a "newest" Task

- `dispatch_review` takes `task_id` as an explicit parameter and loads exactly
  one Task via `store.get(task_id)`. The contract it uses for the Reviewer is
  the `task_binding` of **that one Task**. There is **no code in
  `review_dispatch.py` that enumerates all Tasks sharing a D6 and selects a
  "newest" or "widest" one** — the Task is entirely determined by the
  `task_id` the caller supplies.
- In `_preflight` (`tools/llmtool/llmtool/review_dispatch.py`), the binding is
  read as `binding = task.get('task_binding')` and validated by
  `task_completion.validate_task_binding` (called with
  `contract_digest=binding.get('contract_digest')`). A missing or
  inconsistent binding is refused (`REVIEW_PROMPT_BINDING_MISSING`).
- `dispatch_review` then carries that Task's
  `task_binding.contract_digest`, `task_id` and `run_id` into the claim and
  into the verdict binding line built by
  `_verdict_binding_line` (`tools/llmtool/llmtool/review_dispatch.py`):
  `LLMTOOL-REVIEW-BINDING v1 task=<task_id> run=<run_id>
  contract=<contract_digest> head=<head>`.

### What the Reviewer gets (contract / write scope / goal)

- **Contract** — the single bound Task's `task_binding` (whose
  `contract_digest` is the digest bound above). The dispatch carries
  `task_id`, `run_id` and `contract_digest` from **one** Task; there is no
  union or widening across Tasks in `review_dispatch.py`.
- **Goal / acceptance** — the Reviewer's prompt is the rules prefix
  (from `_review_rules`, `tools/llmtool/llmtool/review_dispatch.py`, built
  from the authenticated `main` commit) concatenated with the rendered review
  prompt. The prompt is rendered by
  `task_submission_cli.run_task_review_prompt` (called from `dispatch_review`)
  with the bound `task_id` / `executor_task` / `reviewer_task` / `pr` /
  `version`. (The prompt renderer's own scoping is outside this read set — see
  the UNKNOWN section.)
- **Write scope** — the Reviewer D6 is created as a **read-only** worktree by
  `_create_d6` (`tools/llmtool/llmtool/review_dispatch.py`) at
  `base_sha=head` (the executor's head / PR head), with the lease
  `"read-only formal review of executor task <executor_task> at <base_sha>"`.
  The Reviewer therefore has no write scope on the PR; the effective scope it
  reviews is the bound Task's contract plus the PR head.

### Where the head (and hence the PR diff) comes from

- The head is the **executor Task's** D6: in `_preflight`
  (`tools/llmtool/llmtool/review_dispatch.py`) the executor D6 registration
  is read via `task_completion.read_d6_registration(executor_task,
  evidence['common_dir'], ...)` and `head = executor.result_sha`; the head is
  checked against a clean git snapshot and against the recorded result
  (`task_completion.result_head_admitted`).
- That same head must equal the **PR head**: `_preflight` fetches
  `pulls/{pr_number}` (`task_completion._github_get`) and refuses
  (`REVIEW_PROMPT_PR_HEAD_MISMATCH`) unless `pull.head.sha == head`. The PR
  `base` sha is also returned and checked.
- So the **PR diff spans the executor's D6** — i.e., whatever the shared D6
  has accumulated across every Task that ran on it — while the **contract is
  the single bound Task's**. That asymmetry is the core of the #1874 symptom
  (next section).

## What happens when several Tasks share the D6

- A follow-up Task records its completion evidence `common_dir` pointing at
  the **same** executor D6, so `_preflight`'s
  `read_d6_registration(executor_task, evidence['common_dir'])` resolves to
  the shared D6 for any of them. The shared D6's accumulated result is what
  the PR head (`head.sha == head`) reflects.
- `dispatch_review` still binds to **exactly one** `task_id` — the one the
  caller passed. Whichever that is, its `task_binding.contract_digest`,
  `task_id` and `run_id` are the only contract values used by the dispatch and
  the verdict binding line. The dispatch does **not** inspect sibling Tasks on
  the D6, does not take the union of their contracts, and does not prefer the
  widest.
- The claim also carries `executor_task`, so the executor D6 used for the
  head/snapshot is the named executor's — again the caller's choice, not a
  code-selected "newest Task".

## A2 — Which #1874 symptom this explains

**Symptom #1874** (the review scope is **narrower than the PR diff** on PR
#1866 and PR #1863) is explained by the review-side facts above:

- The review's contract is **one Task's** `task_binding` (a single
  `contract_digest`), bound in `_preflight` and
  `_verdict_binding_line` (`tools/llmtool/llmtool/review_dispatch.py`).
- When a follow-up Task runs on a D6 that already carries a PR result, the
  PR head — and therefore the PR diff — reflects the **accumulated** work of
  all Tasks on that D6, while the bound contract is **only the dispatched
  (newest) Task's**, which is **narrower**.
- Because the binding is caller-driven (the code does not itself select a
  Task), if the caller dispatches with the newest follow-up Task's `task_id`,
  the Reviewer is scoped to that newer, narrower contract while the PR it
  reviews spans the older, wider diff — exactly the observed
  narrower-than-PR-diff symptom on PR #1866 and PR #1863.

This note **reports the review-side facts only**. It does not propose or
implement a fix.

## UNKNOWN (slice 2 — repair side)

The following are **not** determinable from the read set
(`tools/llmtool/llmtool/review_dispatch.py`) and are marked **UNKNOWN** for
the later repair slice:

- **UNKNOWN:** which caller / CLI layer chooses the `task_id` handed to
  `dispatch_review`, and whether that caller is the one that implements the
  "newest Task on the D6" selection observed in #1874. (Only the review-side
  binding is in scope here; the caller is not.)
- **UNKNOWN:** whether a "newest-Task" rule is *intended* or is itself the
  defect to be repaired; the code shows the selection is caller-driven but not
  where the caller lives.
- **UNKNOWN:** how the prompt renderer
  (`task_submission_cli.run_task_review_prompt`) narrows or widens the review
  scope relative to the PR diff; its internals are outside the read set.
- **UNKNOWN:** whether a Task's `task_binding.contract_digest` is allowed (or
  intended) to be narrower than the PR's accumulated diff, and what the
  intended repair target is (wider binding, union of Task contracts, or a
  caller fix).
- **UNKNOWN:** the repair mechanism itself (slice 2).
