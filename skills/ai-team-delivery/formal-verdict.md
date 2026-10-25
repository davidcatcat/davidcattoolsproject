# Formal verdict comment

Every agent posts to GitHub as one shared account, and GitHub refuses APPROVE from
a PR's own author. A formal Reviewer therefore publishes its verdict as one PR
conversation comment in exactly this format; `llmtool task complete` parses it
strictly (`tools/llmtool/llmtool/task_completion.py`).

```text
<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->
Verdict: PASS | Version: v1 | HEAD: <40-hex result HEAD>
LLMTOOL-REVIEW-BINDING v1 task=<task_id> run=<run_id> contract=<contract_digest> head=<40-hex result HEAD>
FORMAL SECURITY/FUNCTIONAL/QA ACCEPT

<findings, evidence, gates and disposition as free text>
```

- Line 1 is the first non-empty line, byte for byte: `actor` is `Claude` or `Codex`;
  `status` is `PASS`, `FIX` or `FAIL`.
- Line 2 repeats the same status, a version `vN` (N ≥ 1, increase on each re-review)
  and the full result HEAD.
- The binding line uses the Task ID, Run ID and contract digest that the PM gives the
  Reviewer (from `llmtool task show`) and the same HEAD.
- The `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT` line appears **only** on a Claude PASS.
  On FIX/FAIL omit it and put findings in the free text.
- Only a Claude PASS in this exact form satisfies the formal gate. A Codex PASS is
  advisory and is ignored; a later FIX/FAIL (Claude or Codex), a later PR review, or
  any later malformed verdict that names the HEAD supersedes an earlier PASS.
  A PR review and a verdict comment with the same timestamp are not accepted.

## Who may post it

- Only a separate, read-only Reviewer context with its **own D6 Reviewer record**
  (created at the result HEAD with `--github-login` and `--native-session-id`) may
  post a `type=review_verdict` comment.
- Developers and the PM must never post a `type=review_verdict` marker, including
  when relaying or quoting. A PM relaying another agent's text uses its own activity
  type (for example `type=review_gate`).
- The shared login proves nothing about who wrote the comment. Before merging, the
  PM verifies the Reviewer D6 record: it exists, is distinct from the Executor
  (agent, Session and native Session), and its worktree is at the reviewed HEAD.
  `task complete --reviewer-task --pr` checks these; this is process discipline,
  not protection against same-user tampering.
