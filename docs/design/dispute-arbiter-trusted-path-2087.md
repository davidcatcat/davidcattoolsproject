# Trusted dispute-arbiter path (#2087) and dispute supersede (#2088)

Status: **待實作** (design only; no code in this change).

Scope: docs only. This note designs two llmtool changes:

- **A (#2087):** a llmtool step that launches the arbiter itself and records its ruling. The disputing PM never names, binds or vouches for the arbiter run.
- **B (#2088):** a recorded `SUPERSEDED` transition for a dispute made moot by a corrected-contract replacement Task.

Binding Owner decision: ruling C on #1817. **The disputing PM must never bind or vouch for the arbiter.**

Source read for this note: two commits.

- `e7418cf3` (the source commit of the O7 child 1/3 rewrite). `review_dispatch.py`, `review_result.py`, `review_transcript.py` and `task_store.py` were re-read at this commit. Every line cited from these four files refers to `e7418cf3`.
- `208ba7fa` (the earlier repair). Lines cited from `task_submission_cli.py`, `review_repair.py` and `high_debug.py` refer to that commit. They were not re-read in the O7 rewrite, because those files were outside its read scope. The PR that changes each function re-checks its lines.

Files outside both read scopes are marked UNKNOWN where they matter (`lineage_counts.py`, `pm_caller.py`, `role_config.py`, `test_output_excerpts.py`, `scripts/d6_worktree_lifecycle.py`, `scripts/claude_bg_dispatch.py`, merge-gate code).

**Core rule of this design:** a GitHub comment never supplies missing runtime output. The arbiter's ruling comes only from the arbiter runtime's own output: the Codex JSONL that llmtool captured, or the ended Claude session's own transcript. llmtool posts that stored ruling to GitHub only after the claim is durably `COMPLETED` (§2.2 steps 11-12).

---

## 1. Today

### 1.1 Commands

- `task dispute` has only three actions: `_DISPUTE_ACTIONS = ('open', 'defer', 'rule')` (`tools/llmtool/llmtool/task_submission_cli.py:2876`). The parser is at `task_submission_cli.py:1033-1059`.
- The module comment says these commands "record and gate only: they never run or launch a Reviewer or an arbiter" (`task_submission_cli.py:2444-2446`).
- `run_task_dispute` checks that the caller's PM session owns the Task (`pm_caller.check_caller`, `task_submission_cli.py:2888`). Then it calls one of the three functions (`task_submission_cli.py:2879-2914`).

### 1.2 `task dispute open`

`dispute_open` (`task_submission_cli.py:2773-2786`):

- Appends an `OPEN` entry through `_dispute_append` (`task_submission_cli.py:2746-2770`). The entry holds `finding_id`, `pr`, `head_sha` (from a live PR read, `_live_pr_head`, `:2496-2512`), `review_version` of the disputed FAIL claim, `severity`, `state`, `evidence_ref` and `opened_at`. The store write is a compare-and-update on `record_sha256` (`:2768-2769`).
- Computes the arbiter with `_dispute_arbiter` (`:2739-2743`). That calls `choose_arbiter` (`review_repair.py:143-167`), which picks a Reviewer whose family differs from the Developer's, and from the first Reviewer's when possible. Otherwise it picks the first Reviewer again in a fresh session. The first review session is always excluded.
- Returns the arbiter and `'review_launched': False` (`:2786`). Nothing is launched. The chosen arbiter is **not persisted** on the entry; it is recomputed at `rule` time (`:2839`).

### 1.3 `task dispute rule`

`dispute_rule` (`task_submission_cli.py:2798-2873`). The PM supplies `--outcome`, `--reviewer`, `--reviewer-task`, `--reviewer-session` and `--evidence-ref` (parser `:1051-1059`). llmtool then checks:

1. Exactly one `OPEN` entry exists for (pr, finding) at the live head. An `OPEN` entry on an earlier head refuses `DISPUTE_STALE` (`:2824-2829`).
2. `reviewer` equals the recomputed arbiter (`:2839-2841`).
3. The run is fresh: its session is neither the excluded first-review session nor any other recorded review session or Reviewer D6 of the PR (`:2842-2852`).
4. **Arbiter registration** (`_dispute_arbiter_registration`, `:2531-2572`; core checks `:2540-2565`):
   - `reviewer_task` must be a registered D6 Reviewer record (`read_d6_registration`, `:2546-2547`) whose `trusted_agent_id` is the arbiter, with `result_sha` equal to the disputed head and a matching live git snapshot (`:2552-2560`).
   - Its `native_session_id` must equal the PM-supplied `reviewer_session` (`:2561-2563`).
   - The session must not be a Developer session (`:2564-2571`).
5. **Ruling claim** (`_dispute_ruling_claim`, `:2595-2646`): exactly one `review_dispatch_claim` names that Reviewer D6 or session. It must be `COMPLETED`, bound to this Task, run, contract, PR (`claim.pr`), head (`claim.head_sha` and `review_result.head`) and Executor (`claim.executor_task`), and have a later version than the disputed review (`:2623-2645`). Its Reviewer must be the arbiter by `claim_reviewer(claim)` (`:2638`).
   - `claim_reviewer` (`review_repair.py:125-140`) reads the actor only from a `review_verdict` marker (`_VERDICT_ACTOR`, `review_repair.py:103`). A ruling text starts with a `dispute_ruling` marker, so for a ruling claim it falls back to `account_mode`.
   - That fallback map (`review_repair.py:100-102`) knows only `CODEX_CLI`, `EXPLICIT_CLAUDE_CONFIG_DIR` and `DEFAULT_SECOND_ACCOUNT`. `dispatch_review` records `ROLES`, `CODEX_CLI`, `EXPLICIT`, `LEGACY` or `BUILTIN_DEFAULT` (`review_dispatch.py:1307`, `:1312-1315`).
   - So today a Claude arbiter claim, or a Codex arbiter claim under `ROLES`, gets `None` and is refused `DISPUTE_RULING_CLAIM_INVALID`. Design A must close this (§2.2 step 7, step 12).
6. **Ruling comment** (`_dispute_ruling_comment`, `:2649-2706`): the PM-supplied `evidence_ref` comment is read live. It must be unedited, posted after `opened_at`, and byte-equal to the claim's `review_text`. It must carry the `dispute_ruling` marker, the `Ruling:` header and the binding line.

On success the entry becomes `RULED` with a `ruling` dict (`:2864-2870`), and the result says `formal_pass_required: True`.

### 1.4 What blocks while a dispute is OPEN

- `dispute_open_on(task, pr, head)` (`review_repair.py:72-80`) is true while an `OPEN` entry exists for that PR head. A malformed list also counts as open.
- Review repair refuses `REPAIR_DISPUTE_OPEN` (`review_repair.py:689-691`). The check is skipped for test-failure repairs.
- High Debug refuses `DEBUG_DISPUTE_OPEN` (`high_debug.py:1537-1541`). It refuses before any entry is written, so no Debug is spent.
- `withdrawn_findings` (`review_repair.py:83-93`) counts only `RULED` entries whose ruling is `WITHDRAWN` on the same head.

### 1.5 The gap

- **The PM vouches for the arbiter.** The PM chooses which Reviewer D6, which session and which comment make up "the arbiter run" (`:1056-1059`). The values are checked against records, but the PM still picks and presents them. Owner ruling C forbids this.
- **No llmtool step launches the arbiter.**
  - `dispatch_review` (`review_dispatch.py:1207-1504`) renders only the formal review prompt (`run_task_review_prompt`, `:1349-1356`).
  - `_dispatch_codex` always parses with the formal parser (`_parse_codex_review`, `review_dispatch.py:680-683`). There is no parser argument.
- **No trusted source for a Codex ruling.** The formal Codex parser `_parse_codex_review_inner` (`review_dispatch.py:318-400`) keeps the JSONL envelope rules, which a ruling can reuse: one `thread.started` with a `thread_id` of 16-64 hex or `-` characters (`:331-336`), and the last `agent_message` as the final text (`:337-342`). But its inner object must be exactly `{task_id, run_id, head, verdict, review}` (`:357-359`), with `verdict` in `PASS`/`FIX`/`FAIL`/`BLOCKED` (`:367-369`). The text must start with a `review_verdict` marker and a `Verdict:` line (`:377-383`) and pass the formal classifier (`:389-399`). A ruling body is refused `REVIEW_HEADER_MISMATCH` or earlier.
- **No trusted source for a Claude ruling.** Four facts, together, mean a Claude ruling has no runtime source today:
  - The Claude route stops at `DISPATCHED` with the session from the bridge receipt (`review_dispatch.py:1402-1406`, `:1428-1429`). It never stores a `review_result`.
  - The store allows `COMPLETED` only from `CLAIMED`. The one-claim transition rule needs `before.state == 'CLAIMED'` (`task_store.py:5507-5518`). The only other allowed transition is `UNKNOWN` → `DISPATCHED`, the #1636 settle (`:5519-5538`). No transition leaves `DISPATCHED`, so a Claude claim can never become `COMPLETED`.
  - `record_review_result` reads a stored result only for a `COMPLETED` claim (`review_result.py:359-361`). For every other claim it reads the PR's GitHub comments (`:362-373`) through `_comment_verdict`, which needs a `Verdict: … | Version: … | HEAD: …` line (`:79-84`). A ruling has no such line, so it is never found. With `reviewer_session_done` the call records a `REVIEW_BLOCKED` Reviewer event at that head instead (`:375-378`, `:399-402`). The #1636 settle (`_settle_candidate`, `:182-214`) likewise trusts one bound PASS **comment** on GitHub.
  - The transcript reader extracts only `review_verdict` blocks. `verdict_blocks` splits assistant text on `<!-- activity: type=review_verdict actor=` only (`review_transcript.py:14`, `:52`). `select_verdict` keeps a block only if it has the verdict marker and `_comment_verdict` accepts it (`:76-82`). A `dispute_ruling` block is never extracted.
  - So the only place a Claude ruling can appear today is a GitHub comment. All agents share one GitHub login (#926), so anyone can post that comment. This is why `dispute_rule` must trust a PM-named comment (§1.3 item 6). **This design never fills a missing runtime output from GitHub** (core rule, top of this note).
- **The store refuses every new shape this design needs.** All of these are exact-key or fixed-transition checks today:
  - Claim keys must be exactly `_REVIEW_DISPATCH_CLAIM_FIELDS` (with or without `account` and `review_result`) plus four optional keys (`task_store.py:123-127`, `:2028-2035`). `purpose`, `dispute`, `reviewer` and any ruling evidence key are refused.
  - `review_result` must have exactly `_REVIEW_DISPATCH_RESULT_FIELDS` (`:128-129`, `:2085`), with `verdict` in `PASS`/`FIX`/`FAIL`/`BLOCKED` (`:2091`). `RULING` and `outcome` are refused.
  - The appended claim must be `CLAIMED` (`:5498-5500`). A changed claim must keep `executor_task`, `pr`, `head_sha`, `version`, `account_mode`, `account`, `claimed_at`, `reviewer_task` and `rules_sha256` (`:5510-5513`). These are the only fixed keys.
  - A dispute entry must have exactly `_REVIEW_DISPUTE_FIELDS`, or those plus `ruling` (`:1415-1416`, `:1497-1499`). The only change allowed is `OPEN` → `RULED`/`WITHDRAWN` adding only `ruling` (`_check_review_disputes_transition`, `:1538-1560`). `arbiter_claim` and `history` are refused.
  - A `ruling` dict must have exactly `_REVIEW_DISPUTE_RULING_FIELDS` (`:1417-1418`, `_valid_review_dispute_ruling` `:1477-1488`).
- **Formal readers take the highest version at a head, or trust the latest claim's text.** Any new claim at the disputed head would be read as a formal review:
  - `_failed_event` (`review_repair.py:325-367`) takes `max(version)` of the claims at the failed head (`:336-344`). It then looks for a verdict comment of that version (`:357-358`) and refuses `REVIEW_FAIL_EVENT_MISSING` when there is none.
  - `_dispute_fail_verdict` (`task_submission_cli.py:2720-2736`) takes `max(version)` of the `COMPLETED` claims (`:2724-2729`). It skips a ruling claim only by its text marker (`_is_ruling_claim`, `:2585-2592`), so a withheld ruling (empty text) is not skipped.
  - `dispatch_review`'s prior-claim branch (`review_dispatch.py:1231-1251`) posts the stored text of any `COMPLETED` claim with the same (pr, head, version).
  - `record_review_result` finds a claim by exact (executor, pr, version) (`_claim_for`, `review_result.py:161-176`), not by max version. Called with the arbiter claim's version, it would read GitHub or record a `REVIEW_BLOCKED` Reviewer event at the disputed head (above).
  - `unposted_verdict` (`review_transcript.py:90-117`) hands the caller a `review_verdict` block from any Claude claim's transcript, checked against that claim's own `version` (`:109-110`). If an arbiter wrote a well-formed verdict block at the arbiter version, this formal path would hand it out for posting as a formal verdict.
  - So an arbiter claim at version max+1 would break review repair after an `UPHELD` ruling, and could be posted or recorded as a verdict. Design A must exclude it from every formal reader (§2.2 step 7, §4 item 5, PRs A1b and A1c).
- **A moot dispute stays OPEN forever.** When a corrected-contract replacement Task (`continues`, `task_submission_cli.py:8021-8034`, `:8063-8064`) produces a new head, nothing can close the old Task's OPEN dispute. The only exits are `RULED` (needs an arbiter on a head nobody will repair) or nothing.

---

## 2. Design A (#2087): `task dispute arbitrate`

### 2.1 Interface

```
llmtool task dispute arbitrate <task_id> --pr <N> --finding <F#> [--pm-session <id>]
```

- The PM supplies **only** Task, PR and finding (and its own caller session for ownership).
- There is no option for a reviewer, Reviewer Task, session, outcome, ruling text or evidence URL. The parser defines none, so argparse rejects them.
- The command is replay-safe: running it again on the same dispute advances or reports the one arbiter run. It never starts a second one (§2.4).

### 2.2 Steps on the first call

Every refusal before step 7 writes nothing.

1. **Caller.** `pm_caller.check_caller` on the Task, as at `task_submission_cli.py:2888`. This authorises the PM to *request* arbitration only. It grants nothing about the run.
2. **Dispute.**
   - Find exactly one `OPEN` entry for (pr, finding) whose `head_sha` equals the live PR head (`_dispute_head`, `:2515-2528`).
   - Refuse with the existing codes: `DISPUTE_NOT_OPEN`, `DISPUTE_STALE`, `DISPUTE_ALREADY_RULED`. Add `DISPUTE_SUPERSEDED` (§3).
3. **Disputed verdict.**
   - Load the `COMPLETED` formal claim with `version == entry.review_version` at that head. Claims with `purpose: 'dispute_ruling'` are skipped.
   - Refuse `DISPUTE_VERDICT_NEWER` if a later formal (non-ruling) `COMPLETED` claim exists at the same head. The dispute argues against a verdict that is no longer current.
4. **Arbiter.**
   - `chosen = _dispute_arbiter(record, claim)` (`:2739-2743`). Only llmtool chooses.
   - Then `_select_reviewer(task, chosen['reviewer'])` (`review_dispatch.py:169-186`) re-applies the family check and Owner worker-dispatch policy.
   - `DISPUTE_ARBITER_UNAVAILABLE` leaves the dispute `OPEN`.
5. **Preflight.** `_preflight` (`review_dispatch.py:222-283`), unchanged. It checks:
   - the Executor D6 is registered and clean;
   - its head equals the PR head;
   - the task binding is valid.

   The preflight head must equal `entry.head_sha`, or the call refuses `DISPUTE_STALE`.
6. **Rules and dispute text.**
   - Build `_review_rules(repo_root)` from authenticated `main` before any write, as at `review_dispatch.py:1317-1319`.
   - Read the PM's dispute text once from the comment named by `entry.evidence_ref` (`review_dispute_ref_parts`). All agents share one login (#926), so the comment must be on this PR, unedited (`updated_at == created_at`) and created no later than `entry.opened_at`. Otherwise the call refuses `DISPUTE_TEXT_INVALID` and writes nothing.
   - Screen it (`_screen_text`) and size-limit it here. A screen refusal refuses with its code (metadata only, no text) and writes nothing. Keep its SHA-256 for step 7.
7. **Arbiter claim (llmtool-recorded).**
   - One compare-and-update on `record_sha256` appends a `review_dispatch_claim` with these fields:
     - the fields every claim has (as at `review_dispatch.py:1321-1325`), which `_dispute_ruling_claim` and `_dispute_review_claims` read: `executor_task` (the Task's `completion.executor_task_id`), `pr`, `head_sha` (= `entry.head_sha`), `claimed_at`, `updated_at`;
     - `purpose: 'dispute_ruling'`. Every formal reader skips a claim with this purpose, whatever its text or state (§1.5, PRs A1b and A1c);
     - `dispute: {pr, finding_id, head_sha, review_version, dispute_text_sha256}`, the digest from step 6;
     - `reviewer`: the arbiter key llmtool chose in step 4 (`claude-reviewer` or `codex-reviewer`). This is recorded because `claim_reviewer` cannot derive it from a ruling claim (§1.3 item 5);
     - `version`: max recorded version of this Executor and PR + 1, so it is always later than `entry.review_version`. The store's (pr, head, version) uniqueness check (`task_store.py:2095-2098`) still applies;
     - `reviewer_task`: `'reviewer-' + os.urandom(10).hex()`, generated by llmtool as at `review_dispatch.py:1320`. This matches the store's `D6_TASK_ID` pattern `[a-z0-9][a-z0-9-]{0,95}` (`task_store.py:58`) and the transcript reader's `reviewer-[0-9a-f]{20}` (`review_transcript.py:13`);
     - `native_session_id: None`, `state: 'CLAIMED'`, `rules_sha256`, `account_mode`, and `account` when the role has one. These are resolved exactly as `dispatch_review` does (`review_dispatch.py:1283-1315`, `:1326-1327`). That includes its refusals: a Claude role with another provider (`:1288-1290`), or with a builtin source or no account directory (`:1291-1293`), refuses before the claim.
   - The same write appends a history event (§2.6) and sets `entry.arbiter_claim = {reviewer_task, version}`. Today the store refuses all three new claim keys (`task_store.py:2028-2035`) and any dispute-entry change other than adding `ruling` (`:1538-1560`). PR A1 adds them (§7).
   - If the entry already has an `arbiter_claim`, this is a replay (§2.4), not a new claim.
8. **D6.**
   - `_create_d6` (`review_dispatch.py:286-305`) creates the arbiter's read-only Reviewer D6 with `base_sha` = the disputed head and `--agent-id` = the chosen Reviewer key.
   - This step itself creates the registered D6 Reviewer record that `_dispute_arbiter_registration` later verifies.
   - The lease text names dispute arbitration of this Task, PR and finding.
   - `_dispute_arbiter_registration` requires the record's `result_sha` to equal the disputed head (`task_submission_cli.py:2555`). Whether `d6_worktree_lifecycle.py` sets `result_sha` to `--base-sha` for a Reviewer D6 is UNKNOWN (out of scope). PR A2 confirms it by test. If it does not, the ruling is refused `DISPUTE_ARBITER_UNVERIFIED` (fail-closed), and the lifecycle change is a separate PR (§8).
9. **Prompt.**
   - `dispute_arbiter.render_ruling_prompt` renders a fixed ruling prompt: rules prefix + the binding line + the disputed `Finding:` line and its section, taken verbatim from the stored claim `review_text`.
   - It adds the PM's dispute text from step 6, fenced as untrusted quoted data. Its digest must still equal the claim's `dispute.dispute_text_sha256`. The text is not re-read, and a replay never launches again (§2.4).
   - The prompt also gives the exact output contract (§2.3).
   - Before launch, preflight runs again. A moved head refuses `DISPUTE_STALE`, as at `review_dispatch.py:1372-1376`. The policy gate is re-read, as at `:1377-1379`.
10. **Launch and bind.** `review_dispatch.dispatch_arbiter` uses the same calls as `dispatch_review`, with the same account environment (`CLAUDE_CONFIG_DIR` from the role, `review_dispatch.py:1380-1386`):
    - **Codex:** `_dispatch_codex` (`:644-683`) gains one keyword argument, `parser`. Its default is `_parse_codex_review`, so every formal call is unchanged. The arbiter passes `dispute_arbiter.parse_codex_ruling` (§2.3). The session is the `thread_id` of the one `thread.started` event in the captured JSONL.
    - **Claude:** `_dispatch` (`:686-705`). The session is the bridge receipt's `actual_session_id` (`:702-705`). A missing or non-`DELIVERED` receipt raises `REVIEWER_DISPATCH_UNKNOWN` (`:700-701`).
    - Then `_bind_d6(reviewer_task, session_id, repo_root)` (`:708-733`). This is the existing trusted bind path through `d6_worktree_lifecycle.py --bind-native-session-task-id`.
    - The session id **only** ever comes from the runtime's own receipt, never from the PM, a model text or GitHub.
    - **No GitHub read happens in steps 7-11.** `dispatch_arbiter` takes no `comment_lister` or `github_get`, and no step from here to step 11 reads a PR comment.
11. **Claim result.** The ruling comes from runtime output only. Each route has one path to `COMPLETED`.
    - **11a. Codex (one CAS, `CLAIMED` → `COMPLETED`).**
      - `parse_codex_ruling` returns `{thread_id, review_text, outcome, output_sha256}`. `output_sha256` is the SHA-256 of the exact captured stdout bytes.
      - `review_text` is screened with `_screen_text(text, 65536)`, as at `review_dispatch.py:1412-1413`.
      - The claim moves `CLAIMED` → `COMPLETED` with `native_session_id = thread_id` and this `review_result`: `{task_id, run_id, contract_digest, head, verdict: 'RULING', outcome, review_text, source: {kind: 'CODEX_JSONL', output_sha256}}`.
      - This transition already exists for formal Codex claims (`task_store.py:5507-5518`). PR A1 adds only the ruling `review_result` shape (§7).
      - A parse refusal or any other exception records `UNKNOWN` with the session if known, as at `review_dispatch.py:1431-1491`. A ruling parse failure carries only a `failure_reason` that is already in `REVIEW_FAILURE_REASONS` (`review_dispatch.py:37-44`): an envelope failure keeps the formal code (`OUTPUT_UNREADABLE`, `OUTPUT_NOT_JSONL`, `THREAD_ID_INVALID`, `MESSAGE_NOT_TEXT`, `NO_FINAL_MESSAGE`, `NO_THREAD_ID`, `VERDICT_NOT_JSON`, `VERDICT_WRONG_FIELDS`, `VERDICT_BINDING_MISMATCH`), and a ruling text that `parse_ruling` refuses uses `REVIEW_HEADER_MISMATCH`. The store accepts exactly that set (`_known_review_failure_reason`, `task_store.py:1219-1224`), so no new reason is needed.
    - **11b. Claude, launch (one CAS, `CLAIMED` → `DISPATCHED`).** After `_dispatch` and `_bind_d6`, the claim moves to `DISPATCHED` with `native_session_id` from the receipt. This transition exists today (`task_store.py:5507-5518`). The first `arbitrate` call then reports `ARBITER_PENDING`. The bridge returns when the prompt is delivered, not when the session ends.
    - **11c. Claude, collection (a later `arbitrate` call; `DISPATCHED` → `COMPLETED`).** `dispute_arbiter.collect_claude_ruling(store, task, entry, claim)` runs these checks in order. Every refusal in C1-C6 writes nothing: the claim stays `DISPATCHED` and the dispute stays `OPEN`. An ended session's transcript does not change, so a refused collection gives the same refusal on every replay. It never falls back to GitHub.
      - **C1 Claim.** The claim is the entry's own `arbiter_claim` (same `reviewer_task` and `version`), with `purpose: 'dispute_ruling'`, `reviewer: 'claude-reviewer'` and `state: 'DISPATCHED'`. `reviewer_task` matches `reviewer-[0-9a-f]{20}`, and `native_session_id` is a lower-case UUID (`review_transcript.py:12-13`, as `unposted_verdict` checks at `:93-95`). Otherwise `DISPUTE_RULING_CLAIM_INVALID`.
      - **C2 Account.** Re-resolve the `reviewer` role as `unposted_verdict` does (`review_transcript.py:96-100`), but strictly:
        - `claim.get('account')` must **equal** `role.get('account')`, and both must be absent together. `unposted_verdict` also accepts a claim with no account (`:98`); the ruling path does not.
        - `account_mode` must equal the mode that `dispatch_review`'s map (`review_dispatch.py:1312-1315`) gives for the role's current source.
        - The provider must be `claude` or unset, the source must not be builtin, and the account directory must be set (as at `:1288-1293`).
        - Otherwise `ARBITER_ACCOUNT_CHANGED` or `REVIEWER_ACCOUNT_UNCONFIGURED`.
      - **C3 Reviewer D6 and session.** Read the claim's Reviewer D6 record with `read_d6_registration(reviewer_task, common_dir, allowed_states=_D6_EVIDENCE_STATES)`, as `_reviewer_session_bound` does (`review_result.py:232-248`). Its `native_session_id` must equal the claim's. Its `trusted_agent_id` must be `claude-reviewer`. Its `result_sha` must equal the disputed head (as `_dispute_arbiter_registration` checks at `task_submission_cli.py:2552-2560`). Otherwise `ARBITER_SESSION_UNBOUND` or `DISPUTE_ARBITER_UNVERIFIED`.
      - **C4 Ended session.** `review_transcript.session_state(session, config_dir)` (`review_transcript.py:18-31`) runs `claude agents --json --all` under that account directory and must return `ENDED` (row state `done` or `stopped`, `:31`). `RUNNING` reports `ARBITER_PENDING`. `UNKNOWN` (no row, bad JSON, non-zero exit, `:24-30`) reports `ARBITER_SESSION_UNKNOWN`. The transcript is read only after `ENDED`, so it is final.
      - **C5 Transcript.** `dispute_arbiter.ruling_blocks(session, config_dir, reviewer_task)` reads the transcript. It uses the same file rule as `verdict_blocks` (`review_transcript.py:34-39`): exactly one path matching `projects/*<reviewer_task>/<session>.jsonl`, not a symlink, a regular file, at most 64 MiB. It is stricter in three ways:
        - it opens the file once with no-follow and reads its bytes once, keeping `output_sha256` of exactly those bytes;
        - it decodes them as strict UTF-8 (`verdict_blocks` uses `errors='replace'`, `:41`);
        - a file with no assistant entry at all is unavailable, not empty.

        Lines that are not an assistant message are skipped, as at `:43-48`. Only `type == 'assistant'` entries are read, and only content items of `type == 'text'` (as at `:45-51`). User entries are never read, and the prompt is a user entry, so the output contract it shows is never taken as output. Any read failure reports `ARBITER_TRANSCRIPT_UNAVAILABLE`.
      - **C6 Exactly one bound ruling.**
        - Each assistant text is split on the `dispute_ruling` marker prefix `<!-- activity: type=dispute_ruling actor=`, as `verdict_blocks` splits on the verdict marker (`:52`). Each block runs from its marker to the first code-fence line or the end of that text item, with trailing whitespace removed.
        - A block is **bound** if it has the exact binding line of this Task, run, contract and disputed head as a whole line.
        - Exactly one bound block must exist. It must also pass `parse_ruling` (§2.3) with `actor='Claude'` and the claim's `dispute.{finding_id, pr}` and `head_sha`.
        - Zero bound blocks reports `ARBITER_RULING_MISSING`, and so does a block bound to another Task, run or head. More than one bound block, including two identical ones, reports `ARBITER_RULING_AMBIGUOUS`. One bound block that `parse_ruling` refuses reports `DISPUTE_RULING_OUTPUT_INVALID`.
        - Any assistant `review_verdict` block bound to this binding line also reports `DISPUTE_RULING_OUTPUT_INVALID`: an arbiter never writes a verdict.
      - **C7 Screen.** `_screen_text(block, 65536)`, as `unposted_verdict` does (`review_transcript.py:111-116`). On a refusal, the CAS of C8 records `verdict: 'BLOCKED'` with empty `review_text`, no `outcome`, and `source: {kind: 'CLAUDE_TRANSCRIPT', output_sha256}`. The block is never stored, posted or returned, and the result reports only `DISPUTE_RULING_WITHHELD`. This matches the Codex withheld form (`review_dispatch.py:1415-1419`).
      - **C8 Durable result (one CAS).** The claim moves `DISPATCHED` → `COMPLETED` in one compare-and-update on `record_sha256`, because the same write changes `review_dispatch_claim` and the entry's `history`. A CAS conflict writes nothing, and the next replay re-reads. The result is `review_result = {task_id, run_id, contract_digest, head, verdict: 'RULING', outcome, review_text: block, source: {kind: 'CLAUDE_TRANSCRIPT', output_sha256}}`. The same write appends an `ARBITER_COMPLETED` (or `ARBITER_WITHHELD`) history event. Every other claim key stays byte-equal. Today the store refuses any transition out of `DISPATCHED` (`task_store.py:5507-5518`). PR A1 permits this one transition **only** for a `purpose: 'dispute_ruling'`, `reviewer: 'claude-reviewer'` claim whose result `source.kind` is `CLAUDE_TRANSCRIPT` (§7 PR A1). Formal claims keep no exit from `DISPATCHED`.
    - **Publication comes after the durable result.** Step 12 posts only the `review_text` of a stored `COMPLETED` `RULING` result. A `BLOCKED` result is never posted. A ruling found on GitHub but not in the runtime output never completes a claim. C1-C8 read no comment, and `record_review_result` and the #1636 settle refuse ruling claims (PRs A1, A1c).
12. **Post and rule.** For a `COMPLETED` arbiter claim:
    - llmtool posts the ruling comment itself through `_ensure_verdict_posted` (`review_dispatch.py:1077-1094`), under the existing post receipt and lock. The receipt name is already per claim: `_verdict_post_name` is `{task_id}-{reviewer_task}` (`review_dispatch.py:868-875`), and the arbiter claim has its own `reviewer_task`. The caller-posting hand-off (`_reserve_caller_post`, `:1049-1074`) is not used, so the PM never receives ruling text to post.
    - **Comment matching must change for ruling claims.** A per-claim receipt does not change how comments are matched:
      - The ruling and the disputed FAIL carry the same binding line, because both bind the same Task, run, contract and head (`_verdict_binding_line`, `review_dispatch.py:997-1015`).
      - `_find_posted_verdict` (`:1018-1041`) treats any comment by the posting login that contains the binding line, but has a different body, as a conflict. All agents share one login (#926), so the disputed FAIL comment is always such a comment.
      - `_post_under_lock` then refuses `VERDICT_POST_CONFLICT` (`:1173-1174`) when no exact ruling exists. With the unchanged matcher, the ruling could never be posted.
    - So `_post_under_lock` chooses the matcher from the claim's own recorded `purpose`. The caller gives no matcher argument, and `purpose` is written by llmtool in step 7.
      - Formal claims keep `_find_posted_verdict` unchanged.
      - A `purpose: 'dispute_ruling'` claim uses a new `_find_posted_ruling(comments, login, text, binding_line, *, finding_id, pr, head)`. The finding, PR and head come from the claim's `dispute` and `head_sha`, never from model text.
    - `_find_posted_ruling` first selects the **bound ruling-shaped** comments. Each of these:
      - is by the posting login;
      - carries the binding line as a whole line;
      - has a `dispute_ruling` marker line or any `Ruling: ` line.
    - Each selected comment then falls into one of three cases:
      - **Another finding.** It has exactly one `Ruling:` line, that line parses, and it names another finding, PR or head. It is outside the identity and is ignored.
      - **This finding.** It has exactly one `Ruling:` line, that line parses, and it names this finding, PR and head. It is in the **ruling identity**.
      - **Unclear.** Its `Ruling:` line cannot be parsed, it has none, or it has more than one. It counts as a conflict (fail-closed).

      Within the ruling identity, `_find_posted_verdict`'s duplicate and conflict checks stay as they are:
      - a body byte-equal to `text` is the match;
      - a second exact match is `VERDICT_POST_DUPLICATE`;
      - a different body is `VERDICT_POST_CONFLICT`.
    - Bound comments outside the identity are neither a match nor a conflict for the ruling post. These are the disputed FAIL, any other `review_verdict` comment, and rulings on other findings.
    - Formal posting is unchanged. `_find_posted_verdict` still treats a ruling comment at the head as a conflict. Today, a later formal verdict at the same head already conflicts with the disputed FAIL comment, so the ruling comment adds no new refusal case.
    - The evidence comment id is the one llmtool's own receipt records, not a URL from the PM. Only a post result of `POSTED` with `verified: True`, or `ALREADY_POSTED`, that carries an integer `comment_id` (`review_dispatch.py:1133-1134`, `:1166-1172`, `:1195-1199`) counts.
    - `IN_PROGRESS`, `POSTED_UNVERIFIED`, a result without a comment id, or any other post failure (`VERDICT_POST_*`) reports `ARBITER_POST_PENDING` and writes no ruling. The dispute stays `OPEN`, and a later `arbitrate` replays step 12 under the same receipt (§2.4). It never posts a second time.
    - `VERDICT_POST_CONFLICT` or `VERDICT_POST_DUPLICATE` within the ruling identity reports `DISPUTE_RULING_POST_CONFLICT` and writes no ruling. The dispute stays `OPEN`, and the PM reports it to the Owner. A replay re-lists the comments and refuses again while the conflict remains. It never posts over a conflict.
    - llmtool builds `evidence_ref` itself as the PR comment URL of the trusted slug (`_dispute_repository`, `task_submission_cli.py:2468-2481`), the PR and that comment id. The URL must pass `valid_review_dispute_ref`, as at `:2491`. The existing refusal of an `evidence_ref` equal to the entry's own (`:2831-2832`) stays.
    - llmtool then runs the existing verification as one internal function `_dispute_rule_verified(...)` factored out of `dispute_rule`: freshness (`:2842-2852`), `_dispute_arbiter_registration`, `_dispute_ruling_claim` and `_dispute_ruling_comment`. The checks stay as they are, with one addition and one change:
      - **Addition:** the claim found must be the entry's own `arbiter_claim` (same `reviewer_task` and `version`) with `purpose: 'dispute_ruling'`.
      - **Change, for `purpose: 'dispute_ruling'` claims only:** the Reviewer check at `:2638` reads the claim's recorded `reviewer` instead of `claim_reviewer(claim)`. It still requires that key to equal `arbiter['reviewer']`, and the ruling marker's actor (`Claude`/`Codex`) must map to the same key. Formal claims keep `claim_reviewer` unchanged. This is not a weaker check: the recorded `reviewer` is written by llmtool in step 7, never by the PM or the model, and the model's own actor must still agree.
    - The argument checks that `dispute_rule` applies today (`D6_TASK_ID` for the Reviewer Task and `valid_review_dispute_session` for the session, `:2815-2818`) also apply to the values from llmtool's own records. `'reviewer-' + 20 hex` matches `D6_TASK_ID` (`task_store.py:58`). `valid_review_dispute_session` (`task_store.py:1470-1474`) accepts 1-256 printable ASCII characters other than space that pass `_emitted_sensitive`. A Codex `thread_id` (16-64 hex or `-`, `review_dispatch.py:334`) and a Claude UUID session (`review_transcript.py:12`, C1) fit that shape. Whether `_emitted_sensitive` (in `test_output_excerpts.py`, out of scope) passes them is UNKNOWN. PR A3 pins it by test with fixed placeholder ids. If it refuses one, the ruling fails closed, and any change is its own reviewed PR that never removes the check (§8).
    - It passes `reviewer`, `reviewer_task`, `reviewer_session`, `outcome` and `evidence_ref` from its own records only. `outcome` is the one `parse_ruling` read from the arbiter's text (§2.3).
    - It writes `RULED` in one compare-and-update.

### 2.3 Ruling output contract

The arbiter's text must have these first three non-empty lines, as `_dispute_ruling_comment` already requires (`task_submission_cli.py:2656-2660`):

```
<!-- activity: type=dispute_ruling actor=<Claude|Codex> target=PM status=<UPHELD|WITHDRAWN> -->
Ruling: <F#> <UPHELD|WITHDRAWN> | PR: #<pr> | HEAD: <head>
LLMTOOL-REVIEW-BINDING v1 task=<task> run=<run> contract=<digest> head=<head>
```

Free-text reasons may follow the third line. The text is at most 65536 UTF-8 bytes, which is the store's `review_text` limit (`task_store.py:2092-2093`).

**`parse_ruling`.** One strict parser, `dispute_arbiter.parse_ruling(text, *, actor, finding_id, pr, head, binding_line)`, checks the ruling text for both routes. It reuses `_RULING_MARKER`, `_RULING_HEADER` and `_RULING_ANY_LINE` (`task_submission_cli.py:2575-2581`). It returns the outcome or refuses `DISPUTE_RULING_OUTPUT_INVALID`. It refuses when:

- the first three non-empty lines are not exactly marker, `Ruling:` header and binding line;
- there is not exactly one `Ruling:` line or exactly one `dispute_ruling` marker line;
- the marker `status` and the header outcome differ, or either is outside `UPHELD`/`WITHDRAWN`;
- the marker actor is not `actor`, the chosen arbiter's family (`Claude` for `claude-reviewer`, `Codex` for `codex-reviewer`);
- the finding, PR or head differs from the claim's `dispute.{finding_id, pr}` and `head_sha`, or the binding line is not exactly `binding_line`;
- the text has any `review_verdict` marker line or any `Verdict:` line, so a ruling can never also read as a formal verdict (`_comment_verdict` needs a `Verdict:` line, `review_result.py:79-81`).

The **outcome is parsed from the arbiter's text**. The PM never supplies it.

**Codex: `parse_codex_ruling` on the captured JSONL.** This is a ruling-specific parser. The formal `_parse_codex_review` stays the default and is unchanged (§2.2 step 10).

- Envelope: the same rules as `_parse_codex_review_inner` (`review_dispatch.py:320-350`): bytes up to the same output limit; JSONL lines split only at `\n` (`:325-327`); duplicate JSON keys refused (`_unique_json_object`); exactly one `thread.started` with a valid `thread_id` (`:331-336`); and the last `item.completed` `agent_message` as the final text (`:337-342`). Each failure keeps its formal `failure_reason` code (§2.2 step 11a).
- Inner object: exactly `{task_id, run_id, head, outcome, ruling}`, with no `contract_digest` tolerance (compare `:357-358`). `task_id`, `run_id` and `head` must equal the Task's, as at `:360-366`. Otherwise `VERDICT_WRONG_FIELDS` or `VERDICT_BINDING_MISMATCH`.
- `ruling` is a string of at most 65536 bytes. `parse_ruling(ruling, actor='Codex', …)` must accept it, and its outcome must equal the inner `outcome`. Otherwise `REVIEW_HEADER_MISMATCH`.
- The formal classifier (`review_result._comment_verdict`, `:389-399`) is **not** called. A ruling is never a formal verdict.
- Result: `{thread_id, review_text: ruling, outcome, output_sha256}`, where `output_sha256` is the SHA-256 of the raw stdout bytes.

**Claude: the ended session's transcript.** `ruling_blocks` and the selection in §2.2 step 11c (C5-C6) extract the text. `parse_ruling(block, actor='Claude', …)` then checks it. The bridge receipt, the PR comments and the PM never supply ruling text.

### 2.4 Replay

`arbitrate` on an entry that already has `arbiter_claim` does not start a new run. It follows the claim's state, like the prior-claim branch of `dispatch_review` (`review_dispatch.py:1231-1262`):

| Arbiter claim state | Result |
|---|---|
| `CLAIMED` | becomes `UNKNOWN` (outcome uncertain; never replayed) |
| `DISPATCHED` | Claude only (a Codex claim never rests here): step 11c collection. A refusal there (`ARBITER_PENDING`, `ARBITER_SESSION_UNKNOWN`, `ARBITER_TRANSCRIPT_UNAVAILABLE`, `ARBITER_RULING_MISSING`/`_AMBIGUOUS`, …) writes nothing. Success writes `COMPLETED` once and then goes on to step 12 |
| `COMPLETED`, not yet ruled | step 12 (post once under receipt, verify, rule); no usable comment id reports `ARBITER_POST_PENDING`, a conflict or duplicate in the ruling identity reports `DISPUTE_RULING_POST_CONFLICT`; no write in either case |
| `COMPLETED`, `BLOCKED` | reports `DISPUTE_RULING_WITHHELD`; dispute stays `OPEN` |
| `UNKNOWN` | reports `ARBITER_OUTCOME_UNKNOWN`; dispute stays `OPEN` |

- A dispute whose arbiter claim is `UNKNOWN` or `BLOCKED` stays `OPEN` and keeps blocking.
- A second arbiter run is an Owner decision relayed by the PM. It would need its own later design; this note gives no retry path.

### 2.5 Retire the manual `rule` path

- `task dispute rule` keeps its name, so old scripts fail clearly. It refuses `DISPUTE_RULE_MANUAL_RETIRED` before reading any argument value.
- The `--reviewer`, `--reviewer-task`, `--reviewer-session` and `--outcome` options are removed.
- The verification code stays and is reached only from `arbitrate` step 12.

### 2.6 Recorded evidence

Evidence is recorded in two places: on the arbiter claim when it completes (step 11), and on the dispute entry when it is ruled (step 12). Every value comes from llmtool's own records or runtime output. None comes from the PM or from a GitHub comment, except the comment id of llmtool's own post receipt.

**1. Arbiter claim (written in step 7, completed in step 11).**

| Key | When | Value |
|---|---|---|
| `purpose` | from `CLAIMED` on, never changes | `'dispute_ruling'` |
| `dispute` | from `CLAIMED` on, never changes | exactly `{pr, finding_id, head_sha, review_version, dispute_text_sha256}`, equal to the entry's `pr`, `finding_id`, `head_sha` and `review_version`; the digest is 64 hex |
| `reviewer` | from `CLAIMED` on, never changes | `claude-reviewer` or `codex-reviewer` (`REVIEW_DISPUTE_REVIEWERS`, `task_store.py:1425`) |
| `review_result` | `COMPLETED` only | the **ruling result shape** below |

Ruling result shape (exact keys; formal claims keep `_REVIEW_DISPATCH_RESULT_FIELDS`, `task_store.py:128-129`):

- `verdict: 'RULING'`: exactly `{task_id, run_id, contract_digest, head, verdict, review_text, outcome, source}`. `outcome` is in `REVIEW_DISPUTE_OUTCOMES` (`task_store.py:1424`). `review_text` is non-empty and at most 65536 bytes.
- `verdict: 'BLOCKED'` (withheld): exactly `{task_id, run_id, contract_digest, head, verdict, review_text, source}`, with `review_text == ''` and no `outcome`.
- In both shapes, `task_id`, `run_id`, `contract_digest` and `head` are checked as today (`task_store.py:2086-2090`). `source` is exactly `{kind, output_sha256}`, with `output_sha256` 64 hex. `kind` is `CODEX_JSONL` when `reviewer == 'codex-reviewer'`, and `CLAUDE_TRANSCRIPT` when `reviewer == 'claude-reviewer'`.

**2. `RULED` entry (written in step 12).** The `ruling` dict keeps today's keys (`_REVIEW_DISPUTE_RULING_FIELDS`, `task_store.py:1417-1418`: `outcome`, `head_sha`, `reviewer`, `reviewer_task`, `reviewer_session`, `evidence_ref`, `ruled_at`) and adds these **ruling evidence keys**:

- `path: 'arbitrate'`;
- `arbiter_claim_version`, `rules_sha256`;
- `review_text_sha256`: SHA-256 of the exact UTF-8 ruling text;
- `claim_sha256`: SHA-256 of the canonical JSON (sorted keys, `(',', ':')` separators) of the `COMPLETED` arbiter claim;
- `source_kind`, `output_sha256`: copied from the claim's `review_result.source`;
- `comment_id`: from llmtool's own post receipt;
- `contract_digest`, `run_id`.

Today `_valid_review_dispute_ruling` (`task_store.py:1477-1488`) requires exactly today's keys (`:1479`), so a `RULED` write with these keys would be refused. PR A1 changes it in two ways:

- **Validation (stored records).** Exactly one of two key sets is valid:
  - today's keys, for entries that were ruled manually and are already stored;
  - today's keys plus all the ruling evidence keys above, with `path: 'arbitrate'`.

  Both sets keep today's checks (`:1480-1488`) and are strictly typed: digests are 64 hex, and `comment_id` and `arbiter_claim_version` are positive non-bool ints.
- **Record-level binding, `arbitrate` set only.** `_validate` checks the whole record, so a `RULED` `arbitrate` entry is valid only if the same record holds the entry's own arbiter claim, and that claim:
  - is `COMPLETED` with `review_result.verdict == 'RULING'`;
  - has `version == arbiter_claim_version == entry.arbiter_claim.version` and `reviewer_task == ruling.reviewer_task == entry.arbiter_claim.reviewer_task`;
  - has `native_session_id == ruling.reviewer_session` and `reviewer == ruling.reviewer`;
  - has `review_result.outcome == ruling.outcome` and `review_result.source` equal to (`source_kind`, `output_sha256`);
  - matches the three digests: `sha256(review_text) == review_text_sha256`, `rules_sha256` equal, and the recomputed `claim_sha256` equal;
  - has `contract_digest` and `run_id` equal to the record's.

  So a `RULED` write cannot exist without a runtime-sourced `COMPLETED` ruling claim in the same record.
- **Transition (new writes).** In A1, `_check_review_disputes_transition` (`:1538-1560`) accepts `OPEN` → `RULED` with either set. The manual `rule` command and its existing tests still write the legacy set until A3. A3 retires that writer with its own code before any store read (§2.5). After A3 merges, **PR A4** makes the transition accept a new `RULED` write only with the `arbitrate` key set. A new legacy-set write is then refused `REVIEW_DISPUTE_TRANSITION_INVALID`, while stored legacy entries still validate. A4 changes only `task_store.py` and its test.

**3. `arbiter_claim` and `history` on the entry.** Today the entry keys are exact (`task_store.py:1497-1499`), and the only change allowed is `OPEN` → `RULED`/`WITHDRAWN` adding `ruling` (`:1554-1560`). PR A1 adds:

- `arbiter_claim = {reviewer_task, version}`. It is set once, in the step 7 write (`OPEN` → `OPEN`), and in the same record write the `CLAIMED` arbiter claim with that `reviewer_task` and `version` is appended. It never changes after that.
- An append-only `history` list. Each event has exactly these keys:

  ```
  {event, at, actor, head_sha, refs}
  ```

  - `event` is one of `OPENED`, `DEFERRED`, `ARBITER_CLAIMED`, `ARBITER_DISPATCHED`, `ARBITER_COMPLETED`, `ARBITER_UNKNOWN`, `ARBITER_WITHHELD`, `RULED`, `SUPERSEDED`.
  - `actor` is `pm:<caller session>` for PM-requested steps and `llmtool` for steps llmtool performs.
  - `refs` holds only keys from a fixed set (`reviewer_task`, `version`, `comment_id`, `claim_sha256`, `review_text_sha256`, `output_sha256`, `by_task`), each strictly typed: never model text, never a URL.
  - The store refuses a write whose `history` is not the stored list plus exactly one event (an entry with no `history` counts as an empty list). It also refuses an `ARBITER_*` event whose claim state change is not in the same write.

---

## 3. Design B (#2088): `SUPERSEDED`

### 3.1 Interface

```
llmtool task dispute supersede <old_task_id> --pr <N> --finding <F#> --by <replacement_task_id> [--pm-session <id>]
```

- **Who may call:** a PM session that `pm_caller.check_caller` accepts for **both** the old Task and the replacement Task.
- A supersede carries no ruling, outcome, verdict or free text, so it does not engage Owner ruling C. It still records the PM caller as actor.
- llmtool never supersedes automatically. A replacement Task may exist without its PM wanting the old dispute closed.

### 3.2 Conditions

All conditions are checked under `lineage_counts.acquire_chain_lock` for the chain key. Old and replacement Tasks share one key through `continues`, as review repair does at `review_repair.py:799-808`. Any doubt refuses.

1. **Old entry.**
   - Exactly one entry for (pr, finding) on the old Task is `OPEN`.
   - `RULED` refuses `DISPUTE_ALREADY_RULED`. `DEFERRED` refuses `DISPUTE_NOT_OPEN` (deferral is already non-blocking history).
   - `SUPERSEDED` with an identical evidence digest returns the recorded result and writes nothing. A different digest refuses `DISPUTE_ALREADY_SUPERSEDED`.
2. **No pending ruling.**
   - If the entry's arbiter claim is `COMPLETED` and not yet ruled, refuse `DISPUTE_RULING_PENDING`. A ruling already produced is recorded first through `arbitrate`; it is never hidden by a supersede.
   - `CLAIMED`, `DISPATCHED` or `UNKNOWN` arbiter claims do not block supersede. They stay in history, and a later `arbitrate` finalisation refuses `DISPUTE_SUPERSEDED` (§2.2 step 2), so no ruling is ever recorded for them.
3. **Link.**
   - The replacement record's `continues` must equal `old_task_id` exactly (direct successor only).
   - Both Tasks must have the same `lineage_counts.chain_key`, and it must be unchanged after the lock (as at `review_repair.py:806-808`).
   - The replacement must not be abandoned and must have a valid `task_binding` whose `contract_digest` differs from the old Task's.
   - This digest check is weak on its own: `continues` is part of the contract digest (`task_submission_cli.py:535-542`), so a successor's digest always differs. The real proof that the dispute is moot is the new verified head in item 4.
4. **New head.**
   - The replacement's Executor D6 must be registered (`read_d6_registration`) with a clean snapshot whose head equals `result_sha` (as `_preflight`, `review_dispatch.py:245-256`).
   - That head must differ from the disputed `head_sha` and from every head of the old Task's `review_dispatch_claim` list.
5. **Old Task state.**
   - An abandoned old Task is accepted, because abandonment is the normal fate of a replaced Task. This is the only write allowed on an abandoned record, and it touches only `review_disputes`.
   - Whether `task_store.py` permits it is UNKNOWN; PR B1 must confirm or add it. If the store forbids it, B1 refuses `DISPUTE_TASK_ABANDONED` as today (`:2455-2456`), and that limit is reported to the PM.

### 3.3 Write

One compare-and-update on the **old** Task's `record_sha256`:

- `state: 'SUPERSEDED'`.
- `superseded = {by_task, by_run, by_contract_digest, new_head, old_head, old_contract_digest, superseded_at, evidence_sha256}`. `evidence_sha256` is the SHA-256 of the canonical JSON of the other fields except the time.
- One `SUPERSEDED` history event.

The replacement Task is not written. It finds the old dispute through `continues`.

### 3.4 What supersede never does

- **It never rules.** No `ruling` key is set. `withdrawn_findings` (`review_repair.py:83-93`) still ignores the entry, so the finding is never treated as withdrawn.
- **It never lifts a refusal on the old head.**
  - `dispute_open_on` (`review_repair.py:72-80`) keeps its name and callers, but now returns true for `state in ('OPEN', 'SUPERSEDED')` on that PR head. A malformed list still counts as blocking.
  - Review repair (`review_repair.py:690`) and High Debug (`high_debug.py:1540`) keep refusing on the old head. They gain distinct codes `REPAIR_DISPUTE_SUPERSEDED` / `DEBUG_DISPUTE_SUPERSEDED` so the cause is clear.
  - `open`, `defer` and `arbitrate` on that (pr, finding, head) refuse `DISPUTE_SUPERSEDED`.
  - Any merge-gate reader of `review_disputes` (outside read scope, UNKNOWN) must treat `SUPERSEDED` at least as strictly as `OPEN`. PR B1 greps every reader and pins each one by test.
- **It never touches counts.** No repair, review-round, High Debug or lineage count is written, reset or lowered.

---

## 4. Invariants

1. **No ruling without a verified arbiter run.** `RULED` is written only by `arbitrate` step 12. That step requires all of the following:
   - a `COMPLETED` `purpose: 'dispute_ruling'` claim created by llmtool for this entry, with the llmtool-recorded `reviewer` equal to the chosen arbiter and to the ruling marker's actor;
   - a registered Reviewer D6 that llmtool created at the disputed head;
   - a native session bound by `_bind_d6` from the runtime receipt;
   - a ruling comment llmtool posted itself whose body equals the claim text byte for byte;
   - every check of `_dispute_rule_verified`.
2. **The disputing PM cannot pass a session id or a verdict.** No command accepts a reviewer, Reviewer Task, session, outcome, ruling text or ruling evidence URL. `task dispute rule` is retired (§2.5).
3. **The PM cannot choose the arbiter.** `choose_arbiter` is pure and reads only records (`review_repair.py:143-167`). Policy is re-read before each side effect.
4. **Head binding.** The same head must hold at every step: live PR head, Executor D6 head, entry `head_sha`, Reviewer D6 base and `result_sha`, claim `head_sha`, `review_result.head`, the binding line and the `Ruling:` header.
5. **Nothing resets.**
   - Counts, the `continues` lineage, `review_dispatch_claim` versions and the old head's refusals never reset or decrease.
   - A `purpose: 'dispute_ruling'` claim is never a formal verdict, in any state and with any text (withheld text included). Every formal reader excludes it by `purpose`, not only by text marker (`_is_ruling_claim`, `task_submission_cli.py:2585-2592`): `_failed_event` (`review_repair.py:336-344`), `_dispute_fail_verdict` (`task_submission_cli.py:2724-2729`), the disputed-claim lookup of `arbitrate` step 3, and `dispatch_review`'s prior-claim branch (`review_dispatch.py:1231-1262`). The readers in `review_result.py` and `review_transcript.py` are known and covered by PR A1c (§8). Merge-gate readers are outside read scope (UNKNOWN); PR A1b greps every reader of `review_dispatch_claim` and pins each one by test.
   - So after an `UPHELD` ruling, review repair of that head still finds the disputed FAIL verdict and its comment, as it would without the arbiter claim.
   - Whether lineage counting counts it as a review round is UNKNOWN here. PR A1b pins whichever it is by test, and it may only add to a count, never lower one.
6. **A ruling never replaces the formal PASS.** `formal_pass_required: True` stays (`task_submission_cli.py:2873`).
7. **Every transition is audited** in the append-only entry `history` (§2.6). The store refuses rewriting it.
8. **Terminal states.** `RULED` and `SUPERSEDED` are terminal. No transition leaves them.

---

## 5. Threats

| Threat | Defence |
|---|---|
| **Forged session.** PM or another agent presents a session of its choosing. | No option accepts a session. The session comes only from the runtime receipt (`thread.started` / `actual_session_id`), and `_bind_d6` binds it. `_dispute_arbiter_registration` requires the registration's `native_session_id` to equal it (`:2561-2563`). Developer sessions and the first-review session are excluded (`:2564-2571`, `:2850`). |
| **Forged Reviewer D6.** A D6 created by hand at the head. | llmtool generates `reviewer_task` at claim time and records it on the entry before the D6 exists. Verification uses only that id; any other D6 is never consulted. |
| **Forged evidence comment.** All agents share one GitHub login (#926), so someone posts a byte-equal comment, or a different ruling for the same finding. | The comment id comes from llmtool's own post receipt, not a URL argument. Within the ruling identity (§2.2 step 12), `_find_posted_ruling` keeps the poster's duplicate and conflict checks (as in `_find_posted_verdict`, `review_dispatch.py:1018-1041`). A second exact copy, or a different ruling-shaped comment for the same finding, PR and head, refuses `DISPUTE_RULING_POST_CONFLICT`. `_dispute_ruling_comment` still requires an unedited comment posted after `opened_at`. |
| **Disputed FAIL blocks the ruling post.** The FAIL and the ruling share one binding line and one login. | The ruling matcher is chosen from the claim's recorded `purpose`, never from a caller argument. It ignores bound comments outside the ruling identity: the disputed FAIL, other verdicts, and rulings on other findings. Formal posting keeps `_find_posted_verdict` unchanged. |
| **Wrong head.** The PR moves between open, launch and ruling. | Live head checked at steps 2, 5, 9 and 12. A move refuses `DISPUTE_STALE`. The entry stays `OPEN` on the old head; the history keeps the arbiter claim. |
| **Replay of an old ruling.** A ruling from another dispute, PR, Task or run. | The ruling header binds finding, PR and head. The binding line binds task, run and contract. The claim binds `dispute.{pr, finding_id, head_sha, review_version}`. One arbiter claim per entry; `RULED` is terminal. |
| **Replay of `arbitrate`.** It is called twice. | The second call follows the recorded claim (§2.4). A CAS conflict on `record_sha256` refuses the loser before any D6 or launch. |
| **Race with a new formal review.** A new formal review takes the same version, or replays the arbiter claim. | Versions are allocated under the record CAS. `dispatch_review`'s prior-claim branch (`review_dispatch.py:1231-1262`) must refuse `REVIEW_DISPATCH_VERSION_USED` for a `purpose: 'dispute_ruling'` claim, so it never posts ruling text as a verdict. A newer formal verdict at the same head refuses `DISPUTE_VERDICT_NEWER`. |
| **Race supersede vs arbitrate.** Both run at once. | Both write through CAS on the old Task. Whichever commits second re-reads and refuses (`DISPUTE_SUPERSEDED` or `DISPUTE_RULING_PENDING` / `DISPUTE_ALREADY_RULED`). |
| **Supersede used to escape a ruling.** | A pending `COMPLETED` ruling refuses supersede. Supersede lifts no refusal and withdraws no finding. |
| **Forged replacement.** A Task that does not really replace the old one. | The checks are: `continues` equals the old Task exactly, same chain key under the chain lock, a different contract digest, and a registered, clean Executor D6 with a new head. |
| **Prompt injection in the PM's dispute text.** | The text is screened, size-limited and fenced as untrusted data. The output contract is fixed and parsed strictly; anything else is `BLOCKED` and no ruling. |
| **Dispute text swapped after open.** Someone edits the PM's dispute comment, or posts one later and points at it. | The entry's `evidence_ref` is fixed at open. The comment must be unedited and created no later than `opened_at`. Its digest is bound into the arbiter claim before launch, and it is read once (§2.2 step 6). |
| **Arbiter claim read as a formal review.** The arbiter claim has the highest version at the head. | Every formal reader excludes `purpose: 'dispute_ruling'` (§4 item 5, PRs A1b and A1c). A2 does not merge before A1b and A1c, so no arbiter claim exists before the readers skip it. |
| **Sensitive data in ruling text.** | `_screen_text` before storing and posting. Withheld text is never posted (as `review_dispatch.py:1495-1498`). |

---

## 6. Fail-closed cases (summary)

- **`arbitrate`:**
  - caller not owner, bad arguments, abandoned Task;
  - no `OPEN` entry at the live head; stale entry; superseded entry; newer formal verdict;
  - arbiter unavailable or not allowed by policy; family clash;
  - Executor D6 dirty or unregistered; PR head unreadable or moved;
  - rules from `main` unreadable;
  - dispute text unreadable, edited, created after `opened_at`, screened out or too large;
  - claim CAS conflict; D6 create failure; launch failure; bind failure or timeout;
  - Claude collection refusals of §2.2 step 11c (C1-C6: claim, account, Reviewer D6 or session, session not ended, transcript unavailable, missing, ambiguous or invalid ruling);
  - ruling output invalid or withheld; post receipt unavailable, in progress, unverified or without a comment id; a conflict or duplicate within the ruling identity, or an unparsable ruling-shaped bound comment; any verification refusal;
  - store refusal of the arbiter claim's `RULING` result or of the `arbitrate` ruling evidence (A1 schema missing or not matched);
  - arbiter claim without a recorded `reviewer`, or a ruling marker actor that differs from it;
  - Reviewer D6 `result_sha` not equal to the disputed head; generated Reviewer Task or session failing the store's argument shape.

  In every case the dispute stays `OPEN` and keeps blocking.
- **`supersede`:**
  - caller does not own both Tasks;
  - entry not `OPEN`; ruling pending;
  - `continues` mismatch; chain lock unavailable or chain key changed;
  - replacement abandoned, unbound or with the same contract;
  - replacement head unknown, unregistered, dirty or equal to an old head;
  - abandoned old Task not writable (if B1 finds that);
  - CAS conflict.

  In every case the dispute stays as it was.

---

## 7. PRs and tests

Each PR touches at most four files. Tests follow the test data rule (Owner #763 A), all four points:

- no password-, API-key-, token- or credential-shaped strings;
- ids are the fixed placeholder shapes existing dispute tests use;
- runtime, bind, GitHub, account, registry and screen functions are injected or monkeypatched (`dispatch_session`, `bind_session`, `codex_runner`, Codex `subprocess.run`/`shutil.which`, the agents-listing `run`, the role resolver, `read_d6_registration`, `comment_poster`, `comment_lister`, `current_head`, `github_get`, `_screen_text`), and the real behaviour is still checked. The real parsers (`parse_ruling`, `parse_codex_ruling`, `ruling_blocks`) and the real `TaskStore` run in every test that claims them;
- no production check is weakened.

The PM runs the tests on EC2; none were run for this note.

Merge order: A1, A1b, A1c, A2, A2b, A3, A4. B1 needs only A1. A2 must not merge before A1b and A1c, so no arbiter claim is ever stored while a formal reader could still pick it. Which PR carries each change:

| Change | PR |
|---|---|
| Claim keys `purpose`/`dispute`/`reviewer`; ruling `review_result` shape (`RULING`/`BLOCKED`, `outcome`, `source`) | A1 |
| `DISPATCHED` → `COMPLETED` for Claude ruling claims only; no #1636 settle for ruling claims; fixed keys extended | A1 |
| `RULED` ruling evidence keys and record-level binding; `arbiter_claim`; `history`; `SUPERSEDED` | A1 |
| Formal readers in `review_repair.py`, `review_dispatch.py`, `task_submission_cli.py` skip ruling claims | A1b |
| `record_review_result`, `_settle_candidate` and `unposted_verdict` refuse ruling claims | A1c |
| `dispute_arbiter.py` (prompt, `parse_ruling`, `parse_codex_ruling`); `dispatch_arbiter`; `_dispatch_codex` `parser` argument; `arbitrate` steps 1-11a/11b | A2 |
| Claude collection, step 11c (`ruling_blocks`, `collect_claude_ruling`) | A2b |
| Post, verify and rule (step 12); retire manual `rule` | A3 |
| Store refuses a new legacy-set `RULED` write | A4 |

### Proposed module: `tools/llmtool/llmtool/dispute_arbiter.py`

A new module holds every ruling-specific part, so formal review code keeps its behaviour. It has no GitHub I/O and posts nothing. Its only store write is the one step 11c CAS.

| Function | PR | Role |
|---|---|---|
| `render_ruling_prompt(*, rules_prefix, binding_line, finding_section, dispute_text, output_contract)` | A2 | Step 9. Returns the prompt; the dispute text is inside the untrusted fence. |
| `parse_ruling(text, *, actor, finding_id, pr, head, binding_line)` | A2 | §2.3. Returns `outcome` or refuses `DISPUTE_RULING_OUTPUT_INVALID`. |
| `parse_codex_ruling(raw, *, task_id, run_id, contract_digest, head, finding_id, pr)` | A2 | §2.3 Codex. Raises in the formal refusal shape (`review_dispatch_code = 'REVIEW_NOT_POSTED'` plus a `REVIEW_FAILURE_REASONS` reason), so `dispatch_arbiter`'s exception path records `UNKNOWN` as at `review_dispatch.py:1431-1491`. |
| `ruling_blocks(session_id, config_dir, reviewer_task)` | A2b | Step 11c C5. Returns `(output_sha256, assistant_texts)` or `None`. |
| `select_ruling(texts, *, binding_line, actor, finding_id, pr, head)` | A2b | Step 11c C6. Returns `(code, block, outcome)`. |
| `collect_claude_ruling(store, task, entry, claim, *, run=subprocess.run, claude=None, common_dir=None)` | A2b | Step 11c C1-C8. Returns a fixed code, or the `COMPLETED` claim after its one CAS. |

Imports: `review_transcript` (`session_state`, `_UUID`, `_REVIEWER`; read-only use, unchanged), `review_result._reviewer_session_bound`, `task_completion` (`read_d6_registration`, `_D6_EVIDENCE_STATES`, `_review_binding_line`) and `role_config`. `review_dispatch` imports this module, so this module imports `review_dispatch._unique_json_object` only inside `parse_codex_ruling`. That is the same lazy pattern `review_dispatch` uses for `review_result` (`review_dispatch.py:388`).

### PR A1: store schema and transitions (#2087, #2088)

Files:

- `tools/llmtool/llmtool/task_store.py`, all store changes that A1b-A3 and B1 need, so none of them touches this file:
  - **Claim keys.** `purpose`, `dispute` and `reviewer` are optional on `review_dispatch_claim` (`_validate`, `task_store.py:2028-2035`), but only all three together, with `purpose == 'dispute_ruling'`, `reviewer` in `REVIEW_DISPUTE_REVIEWERS` and `dispute` exact as §2.6 item 1.
  - **Ruling `review_result`** (§2.6 item 1). In the `COMPLETED` check (`:2082-2094`), a claim with `purpose` takes the ruling shapes. A claim without it keeps exactly `_REVIEW_DISPATCH_RESULT_FIELDS` and `PASS`/`FIX`/`FAIL`/`BLOCKED`, so `RULING`, `outcome` or `source` on a formal claim is refused.
  - **Fixed keys.** `purpose`, `dispute` and `reviewer` join the keys that the one-claim transition keeps equal (`:5510-5513`).
  - **`DISPATCHED` → `COMPLETED`, ruling claims only.** This is a new branch next to the `CLAIMED` rule (`:5507-5518`). It allows the change only if all of these hold:
    - `before.state == 'DISPATCHED'` and `after.state == 'COMPLETED'`;
    - `before.purpose == 'dispute_ruling'` and `before.reviewer == 'claude-reviewer'`;
    - `before.native_session_id` is a string;
    - `set(after) == set(before) | {'review_result'}`;
    - every key except `state`, `updated_at` and `review_result` is equal;
    - `after.review_result.source.kind == 'CLAUDE_TRANSCRIPT'`;
    - every other claim is unchanged.

    A `CLAIMED` → `COMPLETED` ruling claim needs `source.kind == 'CODEX_JSONL'` and `reviewer == 'codex-reviewer'`. So a Claude ruling must pass through `DISPATCHED` with its bound session. A formal claim keeps no exit from `DISPATCHED`.
  - **No settle for ruling claims.** The #1636 `UNKNOWN` → `DISPATCHED` branch (`:5519-5538`) also requires `'purpose' not in before`. A ruling claim is never settled from a Reviewer event or a GitHub comment.
  - **Dispute entries** (§2.6 items 2-3). These are the `ruling` evidence key set and the record-level binding (`_valid_review_dispute_ruling`, `:1477-1488`; `_validate_review_disputes`, `:1491-1535`), the optional `arbiter_claim` and `history` keys (`:1497-1499`), and the `OPEN` → `OPEN` step 7 change plus history extension (`_check_review_disputes_transition`, `:1538-1560`). New `RULED` writes of the legacy set stay accepted until A4.
  - The terminal `SUPERSEDED` state and its `superseded` key, for B1.
- `tools/llmtool/tests/test_dispute_store_schema.py`.

Tests. These go through the real `TaskStore` (`compare_and_update`, `get`) on a temporary store directory, with no mock of the store. Ids, sessions and digests are fixed placeholder shapes (for example a UUID of zeros with one digit, `'0' * 64`). There are no credential-shaped strings. After each refusal the test checks that the stored record is byte-equal to before.

- **Round trip, Codex ruling.** One write appends a `CLAIMED` ruling claim and sets `arbiter_claim` and an `ARBITER_CLAIMED` event. Then `CLAIMED` → `COMPLETED` with `RULING`, `UPHELD` and `source.kind: 'CODEX_JSONL'` (and again, on a fresh store, with `WITHDRAWN`). Each is read back equal.
- **Round trip, Claude ruling.** `CLAIMED` → `DISPATCHED` with a session, then `DISPATCHED` → `COMPLETED` with `RULING` and `source.kind: 'CLAUDE_TRANSCRIPT'`. It is read back equal. A withheld `BLOCKED` result (empty text, no `outcome`) round-trips on both routes.
- **Round trip, `RULED`.** An entry moves `OPEN` → `RULED` with the full `arbitrate` set bound to its `COMPLETED` claim and is read back equal. A stored entry with today's exact key set still validates.
- **Refusals, transitions:**
  - `DISPATCHED` → `COMPLETED` on a formal claim (formal behaviour unchanged);
  - `DISPATCHED` → `COMPLETED` on a Codex ruling claim, or with `source.kind: 'CODEX_JSONL'`;
  - `CLAIMED` → `COMPLETED` with `source.kind: 'CLAUDE_TRANSCRIPT'`;
  - `DISPATCHED` → `COMPLETED` that changes `native_session_id`, `reviewer_task`, `account`, `account_mode`, `purpose`, `dispute`, `reviewer` or any other key;
  - `UNKNOWN` → `DISPATCHED` settle of a ruling claim, even with a Reviewer event at its head.
- **Refusals, ruling result:**
  - `RULING` without `outcome`;
  - `outcome` outside `UPHELD`/`WITHDRAWN`;
  - `outcome` on `BLOCKED`;
  - `BLOCKED` with text;
  - a missing or extra `source` key, or a `source.kind` that does not match `reviewer`;
  - an extra `review_result` key;
  - `RULING`, `outcome` or `source` on a formal claim.
- **Refusals, claim keys:** a partial `purpose`/`dispute`/`reviewer` set, an unknown `purpose`, a `dispute` that differs from the entry, or a non-64-hex `dispute_text_sha256`.
- **Refusals, `RULED`:**
  - a partial `arbitrate` set, an unknown extra key, or a `path` other than `'arbitrate'`;
  - non-64-hex digests, or a non-int or bool `comment_id`;
  - no `COMPLETED` arbiter claim in the record;
  - a claim whose `outcome`, session, `reviewer`, `reviewer_task`, version, `source`, `review_text` digest or `claim_sha256` differs;
  - a claim that is `BLOCKED`.
- **Refusals, entry:**
  - `history` shortened, rewritten or grown by two events;
  - `arbiter_claim` changed, or set without the matching `CLAIMED` claim in the same write.
- **Formal unchanged:**
  - a formal claim still round-trips `CLAIMED` → `COMPLETED` with each of `PASS`/`FIX`/`FAIL`/`BLOCKED`, and `CLAIMED` → `DISPATCHED`;
  - the #1636 settle of a formal claim is still accepted exactly as today.
- The store accepts `SUPERSEDED` only with a well-formed `superseded` dict and no `ruling` key, and refuses any change to a `RULED` or `SUPERSEDED` entry.

### PR A1b: formal readers skip ruling claims (#2087)

Files:

- `tools/llmtool/llmtool/review_repair.py`: `_failed_event` (`:336-344`) leaves out `purpose: 'dispute_ruling'` claims before taking `max(version)`.
- `tools/llmtool/llmtool/review_dispatch.py`: the prior-claim branch (`:1231-1262`) refuses `REVIEW_DISPATCH_VERSION_USED` for a `dispute_ruling` claim and posts nothing.
- `tools/llmtool/llmtool/task_submission_cli.py`: `_dispute_fail_verdict` (`:2724-2729`) and the disputed-claim lookup (`:2833-2836`) also exclude by `purpose`.
- `tools/llmtool/tests/test_dispute_ruling_claim_readers.py`.

Tests (claims are built in the test as plain records; no runtime is started):

- With a FAIL claim at version N and a `dispute_ruling` claim at N+1 on the same head, `_failed_event` still returns the version-N verdict packet (injected `github_get` lists the version-N comment only). This holds for a `COMPLETED` ruling claim, a withheld one (`BLOCKED`, empty text) and an `UNKNOWN` one.
- `dispatch_review` with the version of a `dispute_ruling` claim refuses `REVIEW_DISPATCH_VERSION_USED`, and the injected `comment_poster` is never called.
- A `dispute_ruling` claim, with or without ruling text, is never picked as the disputed FAIL verdict.
- Without any `dispute_ruling` claim, every reader returns exactly what it returns today (no check weakened).
- Counting with an arbiter claim present never lowers any count.
- The PR description lists every reader of `review_dispatch_claim` found by grep, including merge-gate code. Each one is either changed and tested here or in A1c, or shown not to select by version. If a reader needs a fifth file, it gets its own PR before A2. The readers in `review_result.py` and `review_transcript.py` are already known and are covered by A1c.

### PR A1c: result and transcript readers refuse ruling claims (#2087)

Files:

- `tools/llmtool/llmtool/review_result.py`:
  - `record_review_result` refuses `REVIEW_RESULT_CLAIM_INVALID` for a claim with `purpose`, right after `_claim_for` (`review_result.py:347-348`). This happens before the GitHub read (`:362-373`) and before `record_completion_event` (`:399-402`), so a ruling claim never yields a `REVIEW_BLOCKED` Reviewer event.
  - `_settle_candidate` (`:182-214`) returns `False` for such a claim. This is defence in depth: A1 already refuses the store write.
- `tools/llmtool/llmtool/review_transcript.py`: `unposted_verdict` (`:90-117`) returns `('NOT_APPLICABLE', None, None)` for a claim with `purpose`, before any role, session or transcript read. So no formal post ever comes from an arbiter transcript.
- `tools/llmtool/tests/test_review_readers_skip_ruling_claims.py`.

Tests (real `TaskStore` for `record_review_result`; `github_get`, `run` and `claude` are injected):

- `record_review_result` at the arbiter claim's version refuses `REVIEW_RESULT_CLAIM_INVALID` in each state (`DISPATCHED`, `COMPLETED`, `UNKNOWN`), with and without `reviewer_session_done`. The injected `github_get` is never called, and no completion event is stored.
- **Forged GitHub-only ruling, formal path.** The injected comments hold a byte-equal ruling comment and a well-formed verdict comment at the arbiter version, both bound to the Task. The call still refuses. Nothing is recorded, and the claim stays as stored.
- `unposted_verdict` on a ruling claim returns `NOT_APPLICABLE`. The injected `run` is never called, even when the fixture transcript holds a bound `review_verdict` block at the arbiter version.
- **Formal unchanged.** For formal claims, both functions return exactly today's results on the same fixtures: `COMPLETED` stored result, comment classification, `NOT_READY`, `REVIEW_BLOCKED`, the settle, and every `unposted_verdict` code.

### PR A2: `arbitrate` launch and Codex completion (#2087)

Files:

- `tools/llmtool/llmtool/dispute_arbiter.py` (new): `render_ruling_prompt`, `parse_ruling`, `parse_codex_ruling`.
- `tools/llmtool/llmtool/review_dispatch.py`: `dispatch_arbiter`, reusing `_preflight`, `_create_d6`, `_dispatch`, `_dispatch_codex` and `_bind_d6`. `_dispatch_codex` gains `parser=_parse_codex_review` (`review_dispatch.py:644-683`); every formal call is unchanged.
- `tools/llmtool/llmtool/task_submission_cli.py`: `arbitrate` parser and adapter, steps 1-11a and 11b.
- `tools/llmtool/tests/test_dispute_arbitrate_launch.py`.

A2 ships Codex end to end up to `COMPLETED`. A Claude arbiter claim rests at `DISPATCHED` and reports `ARBITER_PENDING` until A2b merges.

Tests:

- The parser has no reviewer, session, outcome or evidence option, and passing one exits as a usage error.
- llmtool chooses the arbiter (family matrix), and a disallowed policy refuses before any write.
- One claim per entry. A replay in each state follows §2.4, and `CLAIMED` becomes `UNKNOWN`.
- The stored arbiter claim carries `executor_task`, `pr`, `head_sha`, `purpose`, `dispute`, the llmtool-chosen `reviewer`, `account_mode`, `account` (when the role has one) and a version later than the disputed one.
- The arbiter Reviewer D6 is created at the disputed head with the chosen Reviewer key. Its registration `result_sha` equals that head, which pins the §8 point either way.
- A head move before launch refuses `DISPUTE_STALE` with no launch.
- The session recorded is the one the injected runtime returned, and bind is called with it.
- **Bound Codex output succeeds.** `subprocess.run` and `shutil.which` in `review_dispatch` are monkeypatched to return fixed captured JSONL bytes, so the real `_dispatch_codex` → `parse_codex_ruling` path runs. The claim is stored through the real `TaskStore` as `COMPLETED`, with `verdict: 'RULING'`, the parsed `outcome`, `source.kind: 'CODEX_JSONL'` and the SHA-256 of those exact bytes.
- **Refused Codex output.** Each of these records `UNKNOWN` with the expected formal `failure_reason`, posts nothing and leaves the dispute `OPEN`:
  - an inner object with an extra key (including `contract_digest`) or a missing key;
  - two `thread.started` events, or duplicate JSON keys;
  - a mismatched `task_id`, `run_id` or `head`;
  - an inner `outcome` that differs from the ruling text;
  - a ruling with the wrong actor, finding, PR, head or binding line;
  - a text that also holds a `review_verdict` marker or a `Verdict:` line.
- **Formal Codex unchanged.** A formal `dispatch_review` call on the same patched runtime with a formal JSONL fixture returns exactly today's result. `_dispatch_codex` without `parser` still calls `_parse_codex_review`.
- **Claude launch.** An injected `dispatch_session` and `bind_session` give `CLAIMED` → `DISPATCHED` with the returned session. The call reports `ARBITER_PENDING`, and no transcript or comment is read.
- **No GitHub output source.** In steps 7-11, the only GitHub read is the one step 6 read of the PM's dispute-text comment (injected `github_get` called exactly once with that comment's path). `comment_lister` and `comment_poster` are never called.
- A screen refusal (monkeypatched `_screen_text`) on the Codex ruling records `BLOCKED` with empty text, and the dispute stays `OPEN`.
- The dispute text comment (injected `github_get`) that is edited, created after `opened_at`, or refused by a monkeypatched `_screen_text` refuses before any write, D6 or launch. A valid one has its digest on the stored claim, and the rendered prompt carries that exact text inside the untrusted fence.

### PR A2b: Claude ruling collection (#2087)

Files:

- `tools/llmtool/llmtool/dispute_arbiter.py`: `ruling_blocks`, `select_ruling`, `collect_claude_ruling` (§2.2 step 11c).
- `tools/llmtool/llmtool/task_submission_cli.py`: the `arbitrate` replay branch for a `DISPATCHED` arbiter claim (§2.4).
- `tools/llmtool/tests/test_dispute_arbitrate_claude_completion.py`.

Tests. The setup uses:

- a temporary account directory with a fixture transcript at `projects/<dir ending in reviewer_task>/<session>.jsonl`;
- an injected `run` that returns a fixed `claude agents --json --all` row;
- a monkeypatched role resolver and `task_completion.read_d6_registration` returning fixed placeholder records;
- the real `TaskStore`.

Every refusal checks that the stored record is byte-equal to before, that the claim is still `DISPATCHED`, that the dispute is still `OPEN`, and that the injected `github_get`, `comment_lister` and `comment_poster` were never called.

- **Bound native output succeeds.** The session is ended, and the transcript has exactly one assistant ruling bound to this Task, run, contract and head. The claim becomes `COMPLETED` with `RULING`, the parsed `outcome`, the exact block, and `source = {kind: 'CLAUDE_TRANSCRIPT', output_sha256}` (the SHA-256 of the fixture bytes). One `ARBITER_COMPLETED` event is appended. A replay makes no second write.
- **Forged GitHub-only ruling cannot complete.** The comment list holds a byte-equal ruling comment by the shared login. The transcript has no assistant ruling, or has the ruling only in a user entry. Result: `ARBITER_RULING_MISSING`, and no `COMPLETED`, no `RULED` and no post. A later `arbitrate` still refuses the same way.
- **Wrong account:**
  - the role account differs from the claim's, or the claim has none while the role has one: `ARBITER_ACCOUNT_CHANGED`;
  - the `account_mode` differs: `ARBITER_ACCOUNT_CHANGED`;
  - a builtin source or no account directory: `REVIEWER_ACCOUNT_UNCONFIGURED`.
- **Wrong session:**
  - the D6 record's `native_session_id` differs: `ARBITER_SESSION_UNBOUND`;
  - the claim session is not a UUID: `DISPUTE_RULING_CLAIM_INVALID`;
  - there is a transcript only for another session: `ARBITER_TRANSCRIPT_UNAVAILABLE`.
- **Wrong D6:**
  - the record's `trusted_agent_id` is not `claude-reviewer`, or its `result_sha` is not the disputed head: `DISPUTE_ARBITER_UNVERIFIED`;
  - the transcript exists only under another `reviewer_task` directory: `ARBITER_TRANSCRIPT_UNAVAILABLE`.
- **Wrong binding.** A ruling bound to another Task, run, contract or head: `ARBITER_RULING_MISSING`.
- **Ambiguous output.** Two bound rulings, identical or different: `ARBITER_RULING_AMBIGUOUS`.
- **Invalid output.** Each of these gives `DISPUTE_RULING_OUTPUT_INVALID`:
  - a bound ruling with the wrong actor, finding, PR or head;
  - a marker status that differs from the header outcome;
  - a bound `review_verdict` block in the same transcript.
- **Unavailable transcript.** Each of these gives `ARBITER_TRANSCRIPT_UNAVAILABLE`:
  - no file, or two matching files;
  - a symlink;
  - a file over 64 MiB (monkeypatched size);
  - invalid UTF-8;
  - no assistant entry.
- **Session not ended.** `RUNNING` gives `ARBITER_PENDING`. A failing or empty agents listing gives `ARBITER_SESSION_UNKNOWN`. The transcript is not opened in either case.
- **Screen.** A monkeypatched `_screen_text` refusal gives `COMPLETED` with `BLOCKED`, empty text, no `outcome` and the `source` digest. The result is `DISPUTE_RULING_WITHHELD`, and nothing is posted.
- **Formal unchanged.** A formal Claude claim on the same fixtures still rests at `DISPATCHED` (the store refuses its `COMPLETED`), and `unposted_verdict` returns today's result for it.

### PR A3: post, rule, retire manual rule (#2087)

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`: `_dispute_rule_verified`; step 12; `rule` refuses `DISPUTE_RULE_MANUAL_RETIRED`; `ruling` evidence digests.
- `tools/llmtool/llmtool/review_dispatch.py`: `_find_posted_ruling`; `_post_under_lock` picks the matcher from the claim's recorded `purpose` (§2.2 step 12); the post result returns the comment id. `_find_posted_verdict` is not changed.
- `tools/llmtool/tests/test_dispute_arbitrate_rule.py`.
- `tools/llmtool/tests/test_review_dispute.py` (existing dispute tests, if present, updated for retirement only, with no check weakened). If that file has another name, the PR names it; UNKNOWN here.

Tests:

- A full arbitrate run with injected runtime and GitHub writes `RULED` with the evidence keys of §2.6 item 2, bound to its `COMPLETED` claim through the real `TaskStore`. It runs for both a Claude arbiter (`account_mode` `ROLES` or `EXPLICIT`, collected by A2b) and a Codex arbiter (`ROLES` and `CODEX_CLI`). This pins the §1.3 item 5 gap as closed.
- The posted body is the stored claim `review_text`, byte for byte. No step-12 path completes or changes the claim.
- A ruling claim whose recorded `reviewer` differs from the chosen arbiter, or whose marker actor differs from the recorded `reviewer`, refuses `DISPUTE_RULING_CLAIM_INVALID`. A formal claim still goes through `claim_reviewer` unchanged.
- An arbiter claim missing `executor_task`, `pr` or `head_sha` refuses.
- A post result of `IN_PROGRESS`, `POSTED_UNVERIFIED` or one without a comment id reports `ARBITER_POST_PENDING` and writes nothing. The replay then rules without a second post (injected `comment_poster` called once).
- The `evidence_ref` passed to verification is built from the trusted slug and the receipt comment id. The PM-supplied value is never consulted.
- The generated `reviewer_task` and the injected runtime session pass the existing `D6_TASK_ID` / `valid_review_dispute_session` checks. The test pins the shape and keeps the check.
- `rule` with any arguments refuses `DISPUTE_RULE_MANUAL_RETIRED` and writes nothing.
- **Disputed FAIL present.** The injected `comment_lister` already returns, by the same login, the disputed FAIL comment with the same binding line. It also returns a `review_verdict` comment of another version and a ruling on another finding at the same head. The ruling is posted once (injected `comment_poster` called once), the receipt records its comment id, and `RULED` is written. A replay with the posted ruling now in the list returns `ALREADY_POSTED` with the same id and posts nothing.
- **Conflict within the ruling identity.** The list already holds, by the same login, a different ruling-shaped comment for the same finding, PR and head (or an unparsable one). This refuses `DISPUTE_RULING_POST_CONFLICT`, the poster is never called, and nothing is written.
- **Duplicate within the ruling identity.** Two exact copies of the ruling in the list refuse `DISPUTE_RULING_POST_CONFLICT`, and nothing is written.
- **Formal matcher unchanged.** A formal claim posted with the same list still refuses `VERDICT_POST_CONFLICT`, exactly as today. Passing a ruling-shaped list for a formal claim does not switch the matcher.
- An edited comment, or one posted before `opened_at`, refuses.
- A Developer session, the first-review session, or a D6 other than the claimed `reviewer_task` refuses.
- A head move between completion and ruling refuses `DISPUTE_STALE`.
- `formal_pass_required` stays true, and `withdrawn_findings` changes only after `RULED WITHDRAWN`.

### PR A4: store refuses new legacy rulings (#2087)

Files:

- `tools/llmtool/llmtool/task_store.py`: `_check_review_disputes_transition` (`:1538-1560`) accepts a new `RULED` write only with the `arbitrate` key set (§2.6 item 2).
- `tools/llmtool/tests/test_dispute_store_legacy_ruling.py`.

Tests (real `TaskStore`):

- A new `OPEN` → `RULED` write with today's key set is refused `REVIEW_DISPUTE_TRANSITION_INVALID`, and the record is unchanged.
- A stored entry already `RULED` with today's key set still validates and reads back.
- The `arbitrate` write of A1's round trip is still accepted.

### PR B1: `supersede` (#2088)

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`: `supersede` parser, adapter and transition.
- `tools/llmtool/llmtool/review_repair.py`: `dispute_open_on` blocks on `SUPERSEDED`; codes `REPAIR_DISPUTE_SUPERSEDED`.
- `tools/llmtool/llmtool/high_debug.py`: code `DEBUG_DISPUTE_SUPERSEDED` (the check at `:1540` already calls `dispute_open_on`).
- `tools/llmtool/tests/test_dispute_supersede.py`.

The `SUPERSEDED` store schema lands in A1 (§7 PR A1), so B1 does not touch `task_store.py`. If B1 finds that the store forbids the write on an abandoned old Task (§3.2 item 5), B1 refuses that case; any store change for it is a separate PR.

Tests:

- Supersede by a valid direct successor with a new head writes `SUPERSEDED`, the evidence digest and one history event, and leaves the replacement record unchanged.
- Each fail-closed case in §6 refuses with its code and writes nothing.
- An identical replay returns without a write; a different one refuses.
- After supersede, review repair and High Debug on the old head still refuse, `withdrawn_findings` is unchanged, and `open`/`defer`/`arbitrate` refuse `DISPUTE_SUPERSEDED`.
- Counts and `continues` are unchanged.
- A pending `COMPLETED` ruling refuses `DISPUTE_RULING_PENDING`.
- Supersede racing arbitrate (CAS conflict injected) leaves exactly one terminal state.

### PR D1: rule-file text (docs)

Files: `CLAUDE.md`, `AGENTS.md`, `skills/ai-team-delivery/pm-continuation.md`. These describe `arbitrate`, the retired `rule` and `supersede` after A3 and B1 merge. Tests: none (docs only). The existing doc-consistency checks, if any, run in CI.

---

## 8. Open points for review

Resolved by the O7 re-read at `e7418cf3`. These are no longer open:

- A Claude-route ruling cannot complete through `review_result.py`. That module reads stored results only for `COMPLETED` claims, and otherwise reads GitHub comments (`review_result.py:359-373`), which this design never uses for runtime output. Collection lives in `dispute_arbiter.py` (PR A2b). `review_result.py` only refuses ruling claims (PR A1c).
- The readers of `review_dispatch_claim` in `review_result.py` and `review_transcript.py` are `_claim_for` (exact version, `:161-176`), `_completed_claim_result` (`:268-296`), `_settle_candidate` (`:182-214`) and `unposted_verdict` (`review_transcript.py:90-117`). PR A1c covers them.
- The `task_store.py` validators and their lines:
  - claim keys `:2028-2081`;
  - `review_result` `:2082-2094`;
  - claim transitions `:5490-5540`;
  - `ruling` dict `:1477-1488`;
  - dispute entries and transitions `:1491-1560`.

  PR A1 changes them as §7 states.
- `'reviewer-' + 20 hex` matches `D6_TASK_ID` (`task_store.py:58`). The session shape fits `valid_review_dispute_session` (`:1470-1474`). Only its `_emitted_sensitive` part is open (below).

Still open (outside the files read):

- Whether `task_store.py` allows the one `review_disputes` write on an abandoned Task (§3.2 item 5; #2088, not part of the arbiter flow). In the parts of `_update_record` that were read (`task_store.py:5294-5346`, `:5402-5609`), abandonment is refused only for specific writes (`:5334-5341`, `:5488-5489`), and `review_disputes` has no abandonment check (`:5474-5477`). The rest of the write path was not read. B1 pins it by test.
- Whether `_emitted_sensitive` (`test_output_excerpts.py`) passes the generated Reviewer Task and runtime session ids (UNKNOWN; A3 pins it with fixed placeholder ids; a refusal fails closed).
- Whether lineage counting counts a `dispute_ruling` claim as a review round (UNKNOWN; A1b pins it, never lowering a count).
- Which merge-gate code reads `review_dispatch_claim` or `review_disputes` and selects by version (UNKNOWN; A1b and B1 must cover every reader before A2 and B1 merge).
- Whether a Reviewer D6 made by `_create_d6` records `result_sha` equal to its `--base-sha` (UNKNOWN; `d6_worktree_lifecycle.py` is out of scope; A2 pins it, and any lifecycle change is a separate PR).
- `role_config.resolve_role('reviewer')` field names (`account`, `config_dir`, `source`, `provider`) are taken from their use at `review_transcript.py:97-100` and `review_dispatch.py:1284-1315`. `role_config.py` itself was not read. A2b uses them exactly as those call sites do.
