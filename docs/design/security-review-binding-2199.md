# #2199 Security review binding: design

Status: design only (docs). No code changes in this PR.
Source commit read: `0f1014f2` (`main`). Line numbers below are at that commit.

## 1. Problem

The Codex security review is outside TaskStore. Its verdict exists only as a PR
comment posted by the PM helper `codex-sec-review-claude.sh`. As a result:

- A security FAIL creates no `REVIEWER` completion event. `llmtool task repair`
  therefore refuses `REVIEW_FAIL_EVENT_MISSING`, so the FAIL cannot drive a
  bounded repair (PRs #2194 and #2197).
- The merge decision cannot see the security verdict. Any "security PASS" gate
  that exists is a PM convention that reads a comment. Anyone who can post a
  comment of the right shape can forge it (#2202, Codex v91, CWE-345,
  insufficient verification of data authenticity).

Goal: make the security review a TaskStore-bound review like the formal review.
A security FAIL becomes a bound `REVIEW_FAIL` event that drives repair and
blocks merge. Only a bound security PASS at the same head clears it. No GitHub
comment text alone resolves anything.

## 2. Current flow (as read)

### 2.1 Security review today

- The PM runs `codex-sec-review-claude.sh`, which posts a comment. This design
  did not read the helper; it is outside this Task's read scope. Its path,
  prompt and comment shape are UNKNOWN here.
- None of the four modules below creates a claim, an event or a receipt for a
  security review. By convention the comments use version labels from v91 up
  (for example "Security v91 F1" in `review_dispatch.py:428` and "security v92"
  in `review_result.py:191`). No code reserves that range.

### 2.2 Formal review dispatch: `tools/llmtool/llmtool/review_dispatch.py`

- `dispatch_review` (`:1207`) is the single entry point. It does the following
  in order:
  - `_preflight` (`:222`) runs first. It calls `_select_reviewer` (`:169`), which
    enforces family separation and the `worker_dispatch` policy, and checks that
    the Task binding, executor D6 head, PR head and version (1–9999) all match.
  - A claim is keyed by `(pr, head_sha, version)`. A COMPLETED prior claim is
    replayed through `_ensure_verdict_posted` (`:1077`). A version that was
    already used on another head is refused `REVIEW_DISPATCH_VERSION_USED`
    (`:1263`).
  - `_review_rules` (`:109`) builds the rule-card prefix from authenticated
    `main`. The claim records `rules_sha256`.
  - The new claim is written to `review_dispatch_claim` with `state=CLAIMED` and
    `compare_and_update` (`:1321`).
  - Codex route: `_dispatch_codex` (`:644`) runs a read-only `codex exec`.
    `_parse_codex_review_inner` (`:318`) accepts only one exact JSON object with
    these properties:
    - The marker is `type=review_verdict actor=Codex`.
    - The status line is `Verdict: … | Version: vN | HEAD: …`.
    - The text contains the line
      `LLMTOOL-REVIEW-BINDING v1 task= run= contract= head=`.
    - `review_result._comment_verdict` agrees with the verdict.

    The claim becomes `COMPLETED` with a stored `review_result`
    (`task_id, run_id, contract_digest, head, verdict, review_text`, `:1420`).
  - Claude route: `_dispatch` (`:686`) starts a background session. The claim
    becomes `DISPATCHED`, and the verdict is later read from a PR comment.
  - Publication: `_reserve_caller_post` (`:1049`) and `_ensure_verdict_posted`
    (`:1077`, `_post_under_lock` `:1097`) post the stored text once, verbatim,
    under a per-claim lock and receipt (`_verdict_binding_line` `:997`,
    `_find_posted_verdict` `:1018`). The comment is a copy of the bound result.
    It is not its source.

### 2.3 Formal review result: `tools/llmtool/llmtool/review_result.py`

- `record_review_result` (`:332`) finds the claim with `_claim_for` (`:161`), by
  `(executor_task, pr, version)`.
  - COMPLETED claim: `_completed_claim_result` (`:268`) classifies the stored
    `review_text` with `_comment_verdict` (`:42`) using a synthetic comment id
    `0`. No GitHub read happens.
  - Other states: PR comments are read and classified by `_comment_verdict`.
    That function matches the marker, the binding line, the version and head
    line, and a strict formal PASS (`_codex_formal_pass` `:29`,
    `task_completion.is_formal_claude_pass`). It does not check the comment
    author.
- One event is recorded through
  `store.record_completion_event(source='REVIEWER', session_id='review-'+event_id, source_sha=head, payload_sha256=…)`
  (`:399`). The event id is a hash of `{task, run, head, version, outcome,
  comment_id}`. The stored event holds hashes only: it has no `version` and no
  `outcome` field.

### 2.4 Repair: `tools/llmtool/llmtool/review_repair.py`

- `repair_review` (`:632`) uses one of three sources: CI (`_ci_failure` `:428`),
  EC2 (`_remote_test_failure` `:383`) or review (`_failed_event` `:325`).
- `_failed_event` needs a PENDING `REVIEWER` event with
  `session_id == 'review-'+event_id` and a claim on the same head. It then
  re-reads PR comments with `_comment_verdict` and needs the latest bound
  verdict to be `REVIEW_FAIL`. If any of this is missing, it refuses
  `REVIEW_FAIL_EVENT_MISSING`. A comment-only security FAIL has no event and no
  claim, so repair refuses. This is the #2194 and #2197 failure.
- Admission rules:
  - `_review_gate` (`:508`) counts valid FAILs per head, requires blocking
    finding IDs (`review_result.blocking_findings`) and applies
    `repair_policy`.
  - `dispute_open_on` (`:72`) blocks a repair while a dispute is open.
  - The chain lock and the auto limit are applied in `_auto_replay_claim`
    (`:587`) and in the locked block (`:799`).
- With `auto_review`, the next review is dispatched as `dispatch_review(version=packet_version+1)`
  (`:936`). That call has no kind, so it is always a formal review.

### 2.5 Advance and merge: `tools/llmtool/llmtool/task_advance.py`

- `_advance_step` (`:3118`) builds `related` from
  `completion_events + _review_outcomes(...)` (`:3188`–`:3205`; `_review_outcomes`
  `:2620` re-derives `version` and `outcome` from claims and comments). It then
  calls `decide` (`:2385`).
- `decide` and `_pre_review_action` (`:2364`) choose the next step in this
  order:
  1. EC2 remote test.
  2. `DISPATCH_REVIEW`.
  3. `RECORD_REVIEW_RESULT`.
  4. A route based on the outcome:
     - `REVIEW_FAIL` → `REPAIR_REVIEW`
     - `REVIEW_PASS` → `RUN_TASK_COMPLETE`
     - `REVIEW_BLOCKED` → `REVIEWER_ATTENTION`

  The event filter (`:2438`) is `source == 'REVIEWER' and source_sha == head`,
  with `event.get('version', claim.version)`. A stored event has no version, so
  any REVIEWER event at the head matches the newest claim.
- The step bodies are:
  - `DISPATCH_REVIEW` (`:3355`): `version = max(all claims) + 1`.
  - `RECORD_REVIEW_RESULT` (`:3414`).
  - `REPAIR_REVIEW` (`:3464`): `auto=True`.
- Merge: `_merge_once` (`:1482`) calls `_merge_pass_claim` (`:1180`) unless an
  Owner exception applies. That function needs:
  - the newest claim at `(pr, head)` to be PASS with a matching REVIEWER
    outcome;
  - no newer claim of any kind;
  - no newer REVIEWER event.

  It then calls `_merge_checks` (`:572`), which reads the PR files through
  `_pr_files` (`:499`) and applies path rules (`_owner_acceptance_required`
  `:423`, `_required_linux_checks` `:549`).
- Other readers that treat every claim or REVIEWER event as formal:
  - `_head_review_claim` (`:2098`)
  - `_blocked_fallback_reviewer` (`:2511`, counts all claims at the head)
  - `_merged_review_target` (`:2801`)
  - the `REVIEWER_ATTENTION` missing-verdict check (`:3604`)
  - `_pr_number` (`:2532`)

### 2.6 Consequence

None of this code knows a review kind. If you simply wrote a security claim or
event into the existing fields, the formal readers above would take it as a
formal review:

- A security FAIL would look like a formal FAIL.
- A security PASS could satisfy `_merge_pass_claim`.
- A v91 security claim would push the next formal version to 92.

So kind awareness must land before any security record can be written.

## 3. Design

### 3.1 Review kind

- Each entry in `review_dispatch_claim` gets an optional field
  `review_kind: "formal" | "security"`. A missing field means `formal`, so
  existing records stay valid without any rewrite.
- Each `REVIEWER` completion event gets the same optional `review_kind`
  (missing means `formal`). Because stored events hold hashes only, the kind is
  also bound in three more places:
  - The **event identity**: the hashed identity dict gets `"kind": "security"`
    for security events only. Formal identities, and so all existing formal
    event ids, stay byte-identical.
  - The **session id**: `secreview-<event_id>` instead of `review-<event_id>`.
    `_failed_event`'s existing `session_id == 'review-'+event_id` check then
    already excludes security events from the formal path.
  - The **payload**: `payload` gets `"kind": "security"`, so `payload_sha256`
    differs as well.
- One helper, `review_kind(item)` in `review_result.py`, returns `formal`,
  `security` or `None` (a malformed value). Every reader uses it. A `None` kind
  counts as both kinds in every veto (newer-claim checks, merge) and as neither
  kind in every PASS. In other words, it fails closed.

### 3.2 Separate version ranges

- Formal versions are 1–90. Security versions are 91–9999. The 91 floor matches
  the labels the PM helper already uses.
- `dispatch_review` refuses `REVIEW_VERSION_RANGE_INVALID` when the version does
  not match the kind's range. It checks this in `_preflight` (`:281`), before
  any claim exists.
- Next-version arithmetic is per kind. The formal next version is
  `max(formal versions) + 1` (this fixes `task_advance.py:3357`). The security
  next version is `max(91 - 1, max(security versions)) + 1`. A next version
  outside its kind's range is refused `REVIEW_VERSION_RANGE_INVALID`; there is
  no wrap-around.
- Why ranges as well as a kind field: several code paths key on
  `(pr, head, version)` without a kind:
  - `_claim_for`
  - the prior-claim lookup in `dispatch_review` (`:1230`)
  - the refusal receipt name `{task}-{head}-{version}.json` (`:765`)
  - the event identity
  - the `Version: vN` line used by `_may_be_review_verdict` (`:2105`)

  Disjoint ranges keep every such key unique across kinds, including in code
  that has not learned the kind yet.
- Legacy check: a stored claim with no `review_kind` and `version >= 91` is
  ambiguous, and `review_kind` returns `None` for it, which fails closed. Whether
  any such claim exists in a live store is UNKNOWN. PR 1 makes such a claim
  fail closed and rewrites no record. The PM checks live stores for such
  claims before PR 4 lands.

### 3.3 Security dispatch and result

- `dispatch_review(..., kind="security")`:
  - **Route:** the Reviewer is always `codex-reviewer`. It passes the unchanged
    `_select_reviewer` family and policy check. If the Developer family is
    Codex, the dispatch is refused `SECURITY_REVIEWER_SAME_FAMILY`; there is no
    fallback (see §6). The Claude `DISPATCHED` route is never used for
    security, because that route takes its verdict from a PR comment.
  - **Claim:** the claim stores `review_kind: "security"` and the same
    `rules_sha256` (`_review_rules`). The Reviewer D6 is read-only, as for a
    formal review. Its lease text says "security review" instead of "formal
    review" (`_create_d6` `:289`).
  - **Prompt:** the security prompt is rendered by the CLI review-prompt path
    (`task_submission_cli.run_task_review_prompt` gains `kind`). The prompt
    template lives outside this Task's read scope, so its current text is
    UNKNOWN. PR 2 moves the helper's prompt into that path.
  - **Accepted shape:** `_parse_codex_review_inner` takes the kind. For
    security it expects:
    - the marker
      `<!-- activity: type=security_review_verdict actor=Codex target=PM status=PASS|FIX -->`;
    - the same status line;
    - the binding line
      `LLMTOOL-SECURITY-REVIEW-BINDING v1 task= run= contract= head=`;
    - for PASS, the line `SECURITY ACCEPT`.

    Findings use the existing `Finding: F<n> [Severity] …` form, so
    `blocking_findings` and the dispute code work unchanged. The formal
    `_VERDICT_MARKER` and `_CODEX_BINDING` patterns cannot match a security
    body, and the security classifier cannot match a formal body.
  - **Publication:** the result is stored as `review_result` on the COMPLETED
    claim, exactly as for formal Codex. `_verdict_binding_line` and
    `_find_posted_verdict` take the kind, so the publication copy is posted
    once under the existing lock and receipt.
- `record_review_result(..., kind="security")`:
  - It accepts only a `COMPLETED` claim of kind security, at that claim's head
    and version.
  - It classifies only the stored `review_result.review_text`, through a new
    `_security_verdict(text, head, version, task)`.
  - It never calls `github_get`. Any other claim state returns `NOT_READY` or
    `REVIEW_BLOCKED` as today, and never a PASS or a FAIL built from a comment.
  - The outcome is `REVIEW_PASS` only for a strict PASS shape (marker, status,
    binding line and accept line). Every other bound verdict is `REVIEW_FAIL`.

### 3.4 Advance routing (security before formal)

- `_advance_step` computes `related['security']`, which holds two values:
  - **`required`**: true when either of these holds:
    - The PR files at the head touch a rule file. The files are read through
      `_pr_files`, and both `filename` and `previous_filename` are checked.
    - Any security claim already exists for this PR, at any head. This makes
      the requirement sticky, so a repair cannot escape a FAIL by dropping the
      rule file.

    An unreadable file list, or `PR_FILES_TOO_MANY`, means required (fail
    closed).
  - **`latest`**: the newest security claim at `(pr, head)` and its bound
    outcome, built by a security-aware `_review_outcomes`.
- `decide` inserts a security stage after the EC2 PASS and before
  `DISPATCH_REVIEW`. The stage chooses the step as follows:

  | Security state at the current head | Next step |
  |---|---|
  | Required, and there is no security claim | `DISPATCH_SECURITY_REVIEW` |
  | Security claim is `COMPLETED`, and there is no security event | `RECORD_SECURITY_REVIEW_RESULT` |
  | Security claim is `CLAIMED` or `UNKNOWN`, with or without a failure reason | `SECURITY_REVIEW_OUTCOME_UNKNOWN` (stop; no automatic security redispatch in this design) |
  | Security event is `REVIEW_FAIL` | Same repair-claim checks as formal (`REPAIR_RETRY`, `WAITING_REPAIR`, …), otherwise `REPAIR_SECURITY_REVIEW` |
  | Security event is `REVIEW_BLOCKED` | `SECURITY_REVIEWER_ATTENTION` (stop) |
  | Security event is `REVIEW_PASS`, or the review is not required and there is no claim | Fall through to the unchanged formal flow |

- An unresolved bound security FAIL means the newest security claim at the
  current head has a `REVIEW_FAIL` event. While one exists, advance never
  dispatches a formal review. This holds even when the review is not required,
  because a bound FAIL is a fact about the head.
- A formal claim that already exists at the head is left alone. A FAIL is
  routed to repair first anyway: when both kinds FAIL, `REPAIR_SECURITY_REVIEW`
  wins.
- Existing formal `decide` branches filter their claims and events with
  `review_kind == formal`.

### 3.5 Repair from a bound security FAIL

- `repair_review(..., kind="security")` gets its packet from a new
  `_failed_security_event`, which works as follows:
  - It needs a PENDING `REVIEWER` event with
    `session_id == 'secreview-'+event_id` and a COMPLETED security claim at the
    event's head.
  - It re-derives the outcome with `_completed_claim_result(kind=security)`,
    and the result must be `REVIEW_FAIL`.
  - The packet takes the claim's version, `verdict_comment_id: 0`,
    `findings_count`, `review_kind: "security"`, and an excerpt of the stored
    text. The excerpt is untrusted data, as today.

  This path reads no comment. A comment-only FAIL still refuses
  `REVIEW_FAIL_EVENT_MISSING`, which is intended.
- Counting: a security repair is a review repair in the base family `pr-N`.
  `_review_gate` already counts valid FAILs per head, so a head that both kinds
  FAIL counts once. The chain lock, the auto limit (`REVIEW_REPAIR_PM_REQUIRED`),
  RCA, the dispute block and `REPAIR_HEAD_MOVED` all apply unchanged.
- `_record_repair_task` gets acceptance text that names a "security review
  version". The `auto_review` re-dispatch passes `kind=packet.review_kind` with
  the next version of that kind. It no longer dispatches a formal review at
  `packet_version + 1`.

### 3.6 Merge gate

- `_merge_once` calls a new `_merge_security_claim(record, number, head, events)`
  right after `_merge_pass_claim`, on both the normal path and the Owner
  exception path. `_advance_step` passes it `related['security']`. It runs only
  when `related['security'].required` is true, and it returns one refusal code
  or `None`. It refuses in these cases:
  - **`MERGE_SECURITY_REVIEW_MISSING`**: there is no security claim at
    `(pr, head)`.
  - **`MERGE_SECURITY_REVIEW_NOT_PASS`**: the newest security claim at the head
    fails any of these conditions:
    - it is `COMPLETED`;
    - its stored `review_result.verdict` is `PASS`;
    - it has a matching kind-security `REVIEWER` event (by event id) whose
      outcome is `REVIEW_PASS`.
  - **`MERGE_SECURITY_NEWER_REVIEW_EXISTS`**: a newer security claim or event
    exists at that head, in any state. This mirrors `_merge_pass_claim`.
  - **`MERGE_SECURITY_REQUIRED_UNKNOWN`**: the PR file list cannot be read
    completely.
- The gate reads TaskStore and the stored Codex output only. PR comments are
  never an input. That is the fix for the CWE-345 finding: posting a PASS-shaped
  comment changes no record, so it cannot open the gate.
- Rule file set (constant `RULE_FILE_PATHS` in `task_advance.py`): `AGENTS.md`,
  `CLAUDE.md`, and every path under `.claude/skills/`. This is the same set
  `_REVIEW_RULES_HEADER` (`review_dispatch.py:102`) calls rule files. Any wider
  set is an Owner decision (§6).

### 3.7 Invariants

- **I1:** a security claim or event is never read as formal, and a formal one is
  never read as security.
- **I2:** formal behaviour, event ids and versions are unchanged for every record
  without a security claim.
- **I3:** a security verdict exists only as the stored output of a COMPLETED
  Codex claim. A PR comment is a publication copy and resolves nothing.
- **I4:** at any head with an unresolved bound security FAIL, no formal Reviewer
  starts, and repair is reachable through bound events.
- **I5:** a PR that touches a rule file, or that ever had a security claim,
  merges only when the newest bound security verdict at the exact merge head is
  PASS.
- **I6:** an unknown kind, file list or record state fails closed.

## 4. Ordered PRs

Each PR leaves `main` consistent. No writer lands before its readers, and the
gate lands last. Tests follow the test data rule (Owner #763 A):

- Fixtures use the shared TaskStore helpers (`create_with_developer`, #2196),
  hex task, run and head ids, and injected `github_get`, `codex_runner`,
  `comment_poster` and `gh_runner`.
- No credential-shaped strings.
- `_screen_text` is never stubbed to pass. Withheld-text cases inject a
  screen function that refuses.

### PR 1: kind-aware readers and version ranges (no new writer)

- **Files:**
  - `task_store.py`: optional `review_kind` on claims and REVIEWER events, with
    validation. The current event schema is outside this Task's scope
    (UNKNOWN).
  - `review_result.py`: `review_kind` helper; `_claim_for` filters by kind.
  - `review_dispatch.py`: version range check in `_preflight`; a prior claim
    with a different kind is refused `REVIEW_DISPATCH_CLAIM_MISMATCH`.
  - `review_repair.py`: `_failed_event` filters on formal.
  - `task_advance.py`: kind filter in the following:
    - `decide`
    - `_merge_pass_claim`
    - `_review_outcomes`
    - `_merged_review_target`
    - `_blocked_fallback_reviewer`
    - `_head_review_claim`
    - the `REVIEWER_ATTENTION` check
    - the formal next version (`:3357`)
- **Tests:**
  - A security claim and event built by hand are ignored by these formal paths:
    - `decide`, which gives the same step as without them;
    - `_merge_pass_claim`, so a security PASS does not satisfy a formal
      review, and a newer security claim does not veto a formal PASS;
    - `_failed_event`;
    - `_blocked_fallback_reviewer`.
  - With a v91 security claim present, the formal next version is the highest
    formal version + 1.
  - The range refusals behave as specified.
  - Formal event ids for unchanged records are byte-identical (regression).
  - A legacy unkinded claim at version ≥ 91 fails closed.
- **Invariants kept:** I1, I2, I6.

### PR 2: bound security dispatch and result

- **Files:**
  - `review_dispatch.py`: `kind` on `dispatch_review`; Codex-only route;
    security marker, binding line and accept line in
    `_parse_codex_review_inner`; `_verdict_binding_line` and
    `_find_posted_verdict` take the kind; claim `review_kind`.
  - `review_result.py`: `_security_verdict`; `record_review_result(kind=…)`
    with no comment read; kind in the event identity, payload and
    `secreview-` session id.
  - `task_submission_cli.py`: `review-dispatch --kind security`,
    `review-result --kind security`, and the security prompt rendering.
- **Tests:**
  - A canned Codex JSONL PASS or FAIL gives a COMPLETED security claim and one
    kind-security event. Replaying it creates no second event.
  - A security-shaped comment injected through `github_get`, with no claim,
    gives no event.
  - A security FAIL comment that contradicts a stored PASS does not change the
    outcome.
  - A Claude Reviewer and a Codex-family Developer are both refused.
  - A security body is not classified by the formal `_comment_verdict`, and the
    reverse also holds.
  - A binding mismatch is refused `VERDICT_BINDING_MISMATCH`.
- **Invariants kept:** I1, I3. Nothing calls this from advance yet.

### PR 3: advance routing and repair

- **Files:**
  - `task_advance.py`:
    - `related['security']` (from `_pr_files`, sticky);
    - the `decide` security stage;
    - the step bodies `DISPATCH_SECURITY_REVIEW`,
      `RECORD_SECURITY_REVIEW_RESULT` and `REPAIR_SECURITY_REVIEW`;
    - the publication-copy post, using `_caller_posts_verdict` unchanged.
  - `review_repair.py`: `_failed_security_event`; `kind` on `repair_review`;
    the packet `review_kind`; the `auto_review` kind; the repair acceptance
    text.
- **Tests:** `decide` table tests cover every row of §3.4 and check the
  following:
  - No formal `DISPATCH_REVIEW` happens while a bound security FAIL is at the
    head.
  - The rule-file, sticky and unreadable-files cases set `required`.
  - The #2194/#2197 shape works: a bound security FAIL now repairs.
  - A comment-only security FAIL still refuses `REVIEW_FAIL_EVENT_MISSING`.
  - A formal FAIL and a security FAIL on one head count as one valid FAIL.
  - The auto limit applies to security repairs.
  - The `auto_review` re-dispatch is a security review at the next security
    version.
- **Invariants kept:** I1, I3, I4, I6.

### PR 4: merge gate and migration

- **Files:**
  - `task_advance.py`: `RULE_FILE_PATHS`, `_merge_security_claim`, and its call
    in `_merge_once` on both paths.
  - Retiring the comment-posting part of `codex-sec-review-claude.sh`, or
    turning it into a wrapper around `review-dispatch --kind security`. The
    helper's path is UNKNOWN to this design and must be added to that PR's
    write paths.
- **Tests:** each of these cases is refused with its code:
  - A rule-file PR with only a formal PASS.
  - A rule-file PR with a formal PASS and a PASS-shaped security comment but
    no claim (the forgery case).
  - A newer security claim that is FAIL or UNKNOWN.
  - A file list that cannot be read.
  - An Owner exception path with no security PASS.

  These cases are allowed:
  - A bound security PASS at the exact head (merges, with an injected
    `gh_runner`).
  - A non-rule PR with no security claim (unchanged).
  - A PASS at an older head only does not count (refused).
- **Invariants kept:** I5, I6.

## 5. Migration

- **No backfill, no record rewrite.** Claims and events without `review_kind`
  are formal. No comment-only verdict is ever imported into TaskStore, because
  importing one would let comment text resolve a gate (I3).
- **Open PRs with comment-only security verdicts (#2194, #2197 and any other
  open PR; the full list is UNKNOWN):**
  - After PR 3, the next `llmtool task advance` at the current head sees
    `required` (rule file touched) and dispatches a bound security review
    (`DISPATCH_SECURITY_REVIEW`).
  - The earlier PM comment stays as history. It is not read, and it is not
    repaired from.
  - The bound review's FAIL drives repair, and its PASS clears the gate.
  - Manual `task repair` from the old comment still refuses
    `REVIEW_FAIL_EVENT_MISSING`. That is correct, not a regression.
- **Version label overlap:** older comments may already say `v91`. New bound
  security claims also number from 91. This is safe for two reasons:
  security classification never reads comments, and the bound publication copy
  carries the distinct security marker and binding line. The only reader of
  comment version labels, `_may_be_review_verdict`, checks formal versions
  (< 91) only.
- **Between PR 3 and PR 4:** advance already routes security FAILs, but merge
  is not gated yet. A comment-only security verdict is advisory from PR 3 on.
  The PM should rely on the bound result instead.
- **PR 4 rollout:** open rule-file PRs without a bound security PASS at their
  head are refused `MERGE_SECURITY_REVIEW_MISSING` until advance runs the bound
  review. Advance can do that by itself after PR 3, so no manual step is
  needed.
- **Merged PRs:** no retroactive action.
- **Rollback:** reverting PR 4 removes only the gate. Reverting PR 2 or PR 3
  leaves stored security records that PR 1 readers still ignore for formal
  purposes. PR 1 must therefore be reverted last, if at all.

## 6. Out of scope

- A Claude, or any non-Codex, security Reviewer route. A Codex-family
  Developer (for example an OpenCode GPT model) is refused
  `SECURITY_REVIEWER_SAME_FAMILY`, and its rule-file PR stays unmergeable until
  the PM or Owner decides.
- Changes to formal review semantics, the Claude `DISPATCHED` relay
  (`review_transcript.unposted_verdict`), or `_comment_verdict`'s author check.
- The integration-train path (`READY_FOR_INTEGRATION`,
  `_merged_review_target`). It does not merge through `_merge_once`, so a
  security gate there is a follow-up issue. Until then, a train merge of a
  rule-file PR is not security-gated by llmtool.
- Merges made outside llmtool (manual GitHub merges, branch protection).
- Completion gates (`run_task_complete`, `PM_EVIDENCE_READY`).
- A security-specific dispute arbiter. The existing per-head dispute block
  applies to both kinds.
- Requiring security review for every PR, or widening the rule-file set (for
  example to `tools/llmtool/worker-dispatch.json` or other `.claude/` files).
  These are Owner decisions.
- Whether an Owner exception may waive the security gate. This design keeps the
  gate on that path too (fail closed) until the Owner decides.
- Importing or backfilling historic comment-only verdicts.
