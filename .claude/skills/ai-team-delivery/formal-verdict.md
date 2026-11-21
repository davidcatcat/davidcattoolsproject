# Formal verdict comment for `llmtool task complete`

This format applies when an llmtool Task's formal Claude or Codex review is checked by
`llmtool task complete --reviewer-task --pr` (`tools/llmtool/llmtool/task_completion.py`)
and when Codex Reviewer output is parsed by review dispatch
(`tools/llmtool/llmtool/review_dispatch.py`, function `_parse_codex_review_inner` and function `_preflight`).
Every other PR records its review conclusion under the [Transition record](pm-continuation.md#transition-record) rule,
which also governs the marker and `Verdict:` line used here.

## Parser backing and version ranges

Backed by `tools/llmtool/llmtool/review_dispatch.py` functions `_parse_codex_review_inner` and `_preflight`, and `tools/llmtool/llmtool/review_result.py` functions `_comment_verdict`, `_codex_formal_pass`, `finding_ids`, and `record_review_result`:
- **Formal verdict:** version integer must be in range `1 <= version <= 90` (`Version: v1` .. `Version: v90`).
- **Security verdict:** version integer must be in range `91 <= version <= 9999` (`Version: v91` .. `Version: v9999`). Note that `dispatch_review` in `tools/llmtool/llmtool/review_dispatch.py` and `record_review_result` in `tools/llmtool/llmtool/review_result.py` currently enforce `kind == 'formal'`, refusing other kinds with `REVIEW_KIND_UNSUPPORTED`, while kind-aware version validation in `_preflight` retains the security version range.
- **Claude PASS internals:** `review_result._comment_verdict` delegates Claude PASS verification to `is_formal_claude_pass` in `tools/llmtool/llmtool/task_completion.py` (outside this Task's read paths), so any additional checks inside `is_formal_claude_pass` are `UNKNOWN`.

## PR comment shape

Every agent posts to GitHub as one shared account, and GitHub refuses APPROVE from
a PR's own author. The Task's formal Reviewer therefore publishes its verdict as one
new PR conversation comment (<= 65536 bytes UTF-8, checked by `review_result._comment_verdict` and `review_dispatch._parse_codex_review_inner`):

```text
<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->
Verdict: PASS | Version: v1 | HEAD: <40-hex result HEAD>
LLMTOOL-REVIEW-BINDING v1 task=<32-hex task_id> run=<32-hex run_id> contract=<64-hex contract_digest> head=<40-hex result HEAD>
FORMAL SECURITY/FUNCTIONAL/QA ACCEPT

<findings, evidence, gates and disposition as free text>
```

### Minimal valid formal verdict example

```text
<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->
Verdict: PASS | Version: v1 | HEAD: 0123456789abcdef0123456789abcdef01234567
LLMTOOL-REVIEW-BINDING v1 task=0123456789abcdef0123456789abcdef run=fedcba9876543210fedcba9876543210 contract=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef head=0123456789abcdef0123456789abcdef01234567
FORMAL SECURITY/FUNCTIONAL/QA ACCEPT

No findings.
```

### Minimal valid security verdict example

```text
<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->
Verdict: PASS | Version: v91 | HEAD: 0123456789abcdef0123456789abcdef01234567
LLMTOOL-REVIEW-BINDING v1 task=0123456789abcdef0123456789abcdef run=fedcba9876543210fedcba9876543210 contract=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef head=0123456789abcdef0123456789abcdef01234567

Security review only; functional and QA gates not covered. No findings.
```

## Codex Reviewer dispatch JSONL and JSON reply shape

Backed by `tools/llmtool/llmtool/review_dispatch.py` functions `_parse_codex_review_inner` and `_iter_jsonl_lines`:
- Stdout must be a UTF-8 JSONL stream (<= 16 MiB, lines split only on `\n`, each non-empty line a JSON object with unique keys):
  - One `thread.started` event with `thread_id` matching `[0-9a-fA-F-]{16,64}`.
  - At least one `item.completed` event with `item.type == "agent_message"` and string `item.text` (final message <= 1 MiB UTF-8).
- The final `agent_message.text` must be a JSON object with unique keys and exact keys:
  - `task_id` (32-hex string matching task ID)
  - `run_id` (32-hex string matching run ID)
  - `head` (40-hex commit SHA matching result HEAD)
  - `verdict` (string: `'PASS'`, `'FIX'`, `'FAIL'`, or `'BLOCKED'`)
  - `review` (string up to 65536 bytes UTF-8 containing the PR comment format above with `actor=Codex`)
  - `contract_digest` (optional string, value ignored)

### Minimal valid Codex review JSONL example

```jsonl
{"type":"thread.started","thread_id":"01234567-89ab-cdef-0123-456789abcdef"}
{"type":"item.completed","item":{"type":"agent_message","text":"{\"task_id\":\"0123456789abcdef0123456789abcdef\",\"run_id\":\"fedcba9876543210fedcba9876543210\",\"head\":\"0123456789abcdef0123456789abcdef01234567\",\"verdict\":\"PASS\",\"review\":\"<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\\nVerdict: PASS | Version: v1 | HEAD: 0123456789abcdef0123456789abcdef01234567\\nLLMTOOL-REVIEW-BINDING v1 task=0123456789abcdef0123456789abcdef run=fedcba9876543210fedcba9876543210 contract=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef head=0123456789abcdef0123456789abcdef01234567\\nFORMAL SECURITY/FUNCTIONAL/QA ACCEPT\\n\\nNo findings.\"}"}}
```

- The marker is the first non-empty line and the `Verdict:` line the next one, each
  byte for byte, with no added spaces. A non-PASS conclusion uses the marker status and
  `Verdict:` values of the Transition record rule (for example `status=FIX` with
  `Verdict: FAIL`).
- The binding line uses the Task ID, Run ID and contract digest that the PM gives the
  Reviewer (from `llmtool task show`) and the same HEAD.
- The `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT` line appears on formal Claude or Codex
  PASS comments.
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

## Reviewer record for these Tasks

- The `actor=Claude` verdict comes from the separate, read-only Reviewer context that
  has its **own D6 Reviewer record**, created at the result HEAD with `--github-login`
  and `--native-session-id`. Anyone relaying another agent's review (for example the
  PM posting Codex advisory output) labels it with that agent's `actor`.
- Owner decision 2026-11-04 ([#926](https://github.com/davidcatcat/davidcattoolsproject/issues/926)): `ddavidccatssun` is the AI machine account and `davidcatcat` remains the Owner. #926 closed under Owner decision B (link https://github.com/davidcatcat/davidcattoolsproject/issues/926#issuecomment-6697226334): same-user accident guards only, not a security boundary. The AI-account pilot is tracked in #1234 and has not passed, so all agents still post as one login; the shared login proves nothing about who wrote the comment. `task complete
  --reviewer-task --pr` checks that the Reviewer D6 record exists, is distinct from the
  Executor (agent, Session and native Session) and has its worktree at the reviewed
  HEAD. This is process discipline, not protection against same-user tampering.
