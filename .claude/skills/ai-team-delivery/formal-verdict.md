# Formal verdict comment for `llmtool task complete`

This format applies only when an llmtool Task's formal Claude, Codex or AGY review is checked by
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
- The `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT` line appears on formal Claude, Codex
  or AGY PASS comments.
- Each finding has a stable ID and a severity on its own line:
  `Finding: F<n> [<severity>] <file:line> <text>`, numbered F1, F2, ... in order, with
  severity `Blocker`, `High`, `Medium`, `Low` or `Advisory` ([#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502)
  §4.12). `Low` and `Advisory` findings are not blocking; only they may be deferred (E10).
  A dispute, a re-review and an arbitration name findings by these IDs. A verdict whose
  findings carry no IDs counts every finding as blocking.
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

## Exact accepted values (#2423)

- Binding line, whole line, lowercase hex: `task=` 32 hex, `run=` 32 hex, `contract=` 64 hex,
  `head=` 40 hex, nothing after the HEAD (`review_result._CODEX_BINDING`). Placeholders such
  as `task-1` are refused.
- `Version:` is the version the dispatch gave you; a formal review is `v1` to `v90`
  (`review_dispatch._preflight`).
- A Claude PASS passes `task_completion.is_formal_claude_pass`: marker with `actor=Claude`
  and `status=PASS` first, then `Verdict: PASS | Version: vN | HEAD: <result HEAD>`, and the
  exact ACCEPT and binding lines present.
- Security verdicts (`v91` and later) are not dispatchable: `review_dispatch.dispatch_review`
  and `review_result.record_review_result` accept only `kind='formal'` and refuse others
  with `REVIEW_KIND_UNSUPPORTED`.

## Late security verdicts and split children (#2542)

Per the ruling at [#2542](https://github.com/davidcatcat/davidcattoolsproject/issues/2542#issuecomment-7078378897):

- Security re-review after a bound general PASS: the security verdict is posted on the PR
  before the general verdict the gate reads; when it arrives late (after the general PASS
  is bound), the PM dispatches a new general formal version after it, so the newest
  general verdict is again the bound one.
- Split child and ROOT security FAILs: a formal or security verdict binds one PR and one
  exact HEAD, so ROOT FAILs do not bind a split child mechanically. The PM copies each
  open ROOT security finding that falls in the child's scope into the child's acceptance,
  and the child's own security review checks them; a ROOT finding outside every child's
  scope stays recorded on the ROOT and is not silently dropped.

## Codex reply shape

`review_dispatch._parse_codex_review_inner` reads the Codex CLI JSONL stream (the CLI writes
it). Your final agent message must be exactly one JSON object, with no text before or after
it (else `REVIEW_NOT_POSTED`, `VERDICT_NOT_JSON`):

- keys exactly `task_id`, `run_id`, `head`, `verdict`, `review` (an extra `contract_digest`
  is ignored); `task_id`, `run_id` and `head` equal the dispatched values;
- `verdict` is `PASS`, `FIX`, `FAIL` or `BLOCKED`;
- `review` is at most 65536 bytes and is the comment above with `actor=Codex`: marker
  `status=PASS` for PASS, else `status=FIX`; `Verdict: PASS` or `Verdict: FAIL`; at least
  three non-empty lines including the binding line (else `REVIEW_HEADER_MISMATCH` or
  `REVIEW_BINDING_LINE_MISSING`).

Illustration (placeholder IDs; use the dispatched ones):

```json
{"task_id":"<32 hex>","run_id":"<32 hex>","head":"<40 hex>","verdict":"FIX","review":"<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->\nVerdict: FAIL | Version: v1 | HEAD: <40 hex>\nLLMTOOL-REVIEW-BINDING v1 task=<32 hex> run=<32 hex> contract=<64 hex> head=<40 hex>\nFinding: F1 [High] tools/llmtool/llmtool/foo.py:10 <text>"}
```

## AGY Reviewer route (#2938)

`agy-reviewer` is the third formal Reviewer route, after `claude-reviewer` and
`codex-reviewer`; it runs through the AGY CLI on the Claude-family model
`claude-sonnet-5-5-medium`. It is selected only by an explicit request (PM
selection) and is never the implicit default, and it leaves `formal_reviewer_order`
unchanged.

- Its verdict marker actor is `AGY` (`type=review_verdict actor=AGY`); the marker and
  `Verdict:` line follow the same rules as above, with a non-PASS conclusion using the
  Transition record rule values as with the other routes.
- It is Claude family, so it never formally reviews a Claude-authored Task.
- A dispute on an `agy-reviewer` FAIL gets no arbiter of any family
  (`DISPUTE_ARBITER_UNAVAILABLE`).
- An AGY PASS needs the same `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT` line and binding
  line as a Codex PASS.

## Reviewer record for these Tasks

- The `actor=Claude` verdict comes from the separate, read-only Reviewer context that
  has its **own D6 Reviewer record**, created at the result HEAD with `--github-login`
  and `--native-session-id`. Anyone relaying another agent's review (for example the
  PM posting Codex advisory output) labels it with that agent's `actor`.
- Owner decision 2026-11-04 ([#926](https://github.com/davidcatcat/davidcattoolsproject/issues/926)): `ddavidccatssun` is the AI machine account and `davidcatcat` remains the Owner. #926 closed under Owner decision B (link https://github.com/davidcatcat/davidcattoolsproject/issues/926#issuecomment-6697226334): same-user accident guards only, not a security boundary. The AI-account pilot is tracked in #1234 and has not passed, so all agents still post as one login; the shared login proves nothing about who wrote the comment. `task complete
  --reviewer-task --pr` checks that the Reviewer D6 record exists, is distinct from the
  Executor (agent, Session and native Session) and has its worktree at the reviewed
  HEAD. This is process discipline, not protection against same-user tampering.
