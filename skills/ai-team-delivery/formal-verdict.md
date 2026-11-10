# Formal verdict comment for `llmtool task complete`

This format applies only when an llmtool Task's formal Claude or Codex review is checked by
`llmtool task complete --reviewer-task --pr`, which parses it strictly
(`tools/llmtool/llmtool/task_completion.py`). Every other PR records its review
conclusion under the [Transition record](pm-continuation.md#transition-record) rule,
which also governs the marker and `Verdict:` line used here.

Every agent posts to GitHub as one shared account, and GitHub refuses APPROVE from
a PR's own author. The Task's formal Reviewer therefore publishes its verdict as one
new PR conversation comment:

```text
<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->
Verdict: PASS | Version: v1 | HEAD: <40-hex result HEAD>
LLMTOOL-REVIEW-BINDING v1 task=<task_id> run=<run_id> contract=<contract_digest> head=<40-hex result HEAD>
FORMAL SECURITY/FUNCTIONAL/QA ACCEPT

<findings, evidence, gates and disposition as free text>
```

- The marker is the first non-empty line and the `Verdict:` line the next one, each
  byte for byte, with no added spaces. A non-PASS conclusion uses the marker status and
  `Verdict:` values of the Transition record rule (for example `status=FIX` with
  `Verdict: FAIL`).
- The binding line uses the Task ID, Run ID and contract digest that the PM gives the
  Reviewer (from `llmtool task show`) and the same HEAD.
- The `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT` line appears on formal Claude or Codex
  PASS comments.
- A Claude PASS in this exact form, unedited, satisfies the gate. Under [Owner D6
  (#846)](https://github.com/davidcatcat/davidcattoolsproject/issues/846), an
  independent `codex-reviewer` PASS is eligible for Tasks authored through Claude,
  AGY, OpenCode, home LLM `gw-agent` or historical company `gw` Tasks, but never for a Codex-authored Task.
  All Codex PASS comments must meet the registered Reviewer,
  independent D6 record and task-bound completion checks. Under Owner decision
  035:A (#525, c6468216854), for a Claude-authored Task specifically,
  `llmtool task complete` accepts that Codex PASS only when the persisted Task classifies the
  comment as `REVIEW_PASS` and exactly one matching `COMPLETED` review dispatch
  claim binds the PASS and identical non-empty review text to the PR, HEAD and
  Reviewer Task. Other Codex PASS comments are advisory and neither accept nor
  supersede.
  Any other verdict comment that names the HEAD
  (FIX, FAIL, BLOCKED or malformed), an edited verdict comment, or a later PR review
  supersedes an earlier PASS. A PR review and a verdict comment with the same timestamp
  are not accepted.

## Reviewer record for these Tasks

- The `actor=Claude` verdict comes from the separate, read-only Reviewer context that
  has its **own D6 Reviewer record**, created at the result HEAD with `--github-login`
  and `--native-session-id`. Anyone relaying another agent's review (for example the
  PM posting Codex advisory output) labels it with that agent's `actor`.
- Owner decision 2026-11-04 ([#926](https://github.com/davidcatcat/davidcattoolsproject/issues/926)): `ddavidccatssun` is the AI machine account and `davidcatcat` remains the Owner. #926 closed under Owner decision B (link https://github.com/davidcatcat/davidcattoolsproject/issues/926#issuecomment-6697226334): same-user accident guards only, not a security boundary. The AI-account pilot is tracked in #1234 and has not passed, so all agents still post as one login; the shared login proves nothing about who wrote the comment. `task complete
  --reviewer-task --pr` checks that the Reviewer D6 record exists, is distinct from the
  Executor (agent, Session and native Session) and has its worktree at the reviewed
  HEAD. This is process discipline, not protection against same-user tampering.
