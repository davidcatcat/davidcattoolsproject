# Trusted dispute-arbiter path (#2087) and dispute supersede (#2088)

Status: **待實作** (design only; no code in this change).

Scope: docs only. This note designs two llmtool changes:

- **A (#2087):** a llmtool step that launches the arbiter itself and records its ruling. The disputing PM never names, binds or vouches for the arbiter run.
- **B (#2088):** a recorded `SUPERSEDED` transition for a dispute made moot by a corrected-contract replacement Task.

Binding Owner decision: ruling C on #1817. **The disputing PM must never bind or vouch for the arbiter.**

Source read for this note: three commits.

- `c1ab1339` (the source commit of the O7 child 2/3 rewrite). `review_dispatch.py`, `review_repair.py`, `task_advance.py` and `task_submission_cli.py` were re-read at this commit for §2.2 step 3, §2.2 step 12, §2.4, §4 and the race tests. Every line cited from `task_advance.py` refers to `c1ab1339`. The cited lines of the other three files were checked at `c1ab1339` and are unchanged from the commits below.
- `e7418cf3` (the source commit of the O7 child 1/3 rewrite). `review_dispatch.py`, `review_result.py`, `review_transcript.py` and `task_store.py` were re-read at this commit. Lines cited from `review_result.py`, `review_transcript.py` and `task_store.py` refer to `e7418cf3`.
- `208ba7fa` (the earlier repair). Lines cited from `high_debug.py` refer to that commit; it was not re-read. The PR that changes each function re-checks its lines.

Files outside all three read scopes are marked UNKNOWN where they matter (`lineage_counts.py`, `pm_caller.py`, `role_config.py`, `test_output_excerpts.py`, `scripts/d6_worktree_lifecycle.py`, `scripts/claude_bg_dispatch.py`, merge-gate code).

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
  - So an arbiter claim at version max+1 would break review repair after an `UPHELD` ruling, and could be posted or recorded as a verdict. Design A must exclude it from every formal reader (§2.2 step 7, §4 item 10, PRs A1b and A1c).
- **The disputed verdict's identity is not carried through.** A dispute disputes one finding of one formal verdict: (pr, head, review version, finding). Today only part of that identity is used:
  - `withdrawn_findings` (`review_repair.py:83-93`) matches a `RULED` `WITHDRAWN` entry by (pr, head) only. `_review_gate` calls it with the packet's PR and failed head (`review_repair.py:531`), not with the packet's `version` (`:372`). So a finding ID withdrawn from verdict version N would also be withdrawn from a newer formal verdict N+1 at the same head that reuses that ID for a different finding.
  - The ruling header `Ruling: <F#> <OUTCOME> | PR: #<pr> | HEAD: <head>` (`_RULING_HEADER`, `task_submission_cli.py:2578-2580`) and the post receipt identity (`_receipt_identity`, `review_dispatch.py:878-884`) carry no disputed review version.
- **No fresh newer-verdict check at ruling time.** `dispute_rule` reads the record once (`task_submission_cli.py:2819`), verifies against that read, and writes `RULED` with a compare-and-update on its `record_sha256` (`:2869-2870`). It never asks whether a newer formal verdict exists at the head. A formal Claude review leaves its claim `DISPATCHED` and keeps its outcome only in a matching `REVIEWER` completion event (`task_advance.py:1186-1189`), so a check of `COMPLETED` claims alone would miss it.
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
   - The **disputed-verdict identity** is `D = (pr, head_sha, review_version, finding_id)`, read from the entry. Every later step carries all four values (§4 item 5).
   - Load the `COMPLETED` formal claim with `version == entry.review_version` at that head. Claims with `purpose: 'dispute_ruling'` are skipped.
   - Run the newer-outcome check **N** of step 12 (step 12b, item 5) on this record. It refuses `DISPUTE_VERDICT_NEWER` if any newer formal outcome exists at the head, including a Claude `DISPATCHED` claim backed by a `REVIEWER` event. The dispute argues against a verdict that is no longer current, so no arbiter run starts.
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
        - Exactly one bound block must exist. It must also pass `parse_ruling` (§2.3) with `actor='Claude'` and the claim's `dispute.{finding_id, pr, review_version}` and `head_sha`.
        - Zero bound blocks reports `ARBITER_RULING_MISSING`, and so does a block bound to another Task, run or head. More than one bound block, including two identical ones, reports `ARBITER_RULING_AMBIGUOUS`. One bound block that `parse_ruling` refuses reports `DISPUTE_RULING_OUTPUT_INVALID`.
        - Any assistant `review_verdict` block bound to this binding line also reports `DISPUTE_RULING_OUTPUT_INVALID`: an arbiter never writes a verdict.
      - **C7 Screen.** `_screen_text(block, 65536)`, as `unposted_verdict` does (`review_transcript.py:111-116`). On a refusal, the CAS of C8 records `verdict: 'BLOCKED'` with empty `review_text`, no `outcome`, and `source: {kind: 'CLAUDE_TRANSCRIPT', output_sha256}`. The block is never stored, posted or returned, and the result reports only `DISPUTE_RULING_WITHHELD`. This matches the Codex withheld form (`review_dispatch.py:1415-1419`).
      - **C8 Durable result (one CAS).** The claim moves `DISPATCHED` → `COMPLETED` in one compare-and-update on `record_sha256`, because the same write changes `review_dispatch_claim` and the entry's `history`. A CAS conflict writes nothing, and the next replay re-reads. The result is `review_result = {task_id, run_id, contract_digest, head, verdict: 'RULING', outcome, review_text: block, source: {kind: 'CLAUDE_TRANSCRIPT', output_sha256}}`. The same write appends an `ARBITER_COMPLETED` (or `ARBITER_WITHHELD`) history event. Every other claim key stays byte-equal. Today the store refuses any transition out of `DISPATCHED` (`task_store.py:5507-5518`). PR A1 permits this one transition **only** for a `purpose: 'dispute_ruling'`, `reviewer: 'claude-reviewer'` claim whose result `source.kind` is `CLAUDE_TRANSCRIPT` (§7 PR A1). Formal claims keep no exit from `DISPATCHED`.
    - **Publication comes after the durable result.** Step 12 posts only the `review_text` of a stored `COMPLETED` `RULING` result. A `BLOCKED` result is never posted. A ruling found on GitHub but not in the runtime output never completes a claim. C1-C8 read no comment, and `record_review_result` and the #1636 settle refuse ruling claims (PRs A1, A1c).
12. **Post and rule.** This step runs for a `COMPLETED` arbiter claim whose `review_result.verdict` is `RULING`. It runs on the first call right after 11a, and on every replay that finds such a claim (§2.4). It has six parts: 12a identity, 12b the freshness check, 12c the post, 12d the post result, 12e the final check and CAS, and 12f what a refusal leaves behind.

    **12a. Identity.** The call fixes, once, from the record it started with:
    - `D = claim.dispute.{pr, head_sha, review_version, finding_id}`. These must equal the entry's `pr`, `head_sha`, `review_version` and `finding_id`. The entry's `arbiter_claim` must equal the claim's `{reviewer_task, version}`, and `claim.head_sha` and `claim.review_result.head` must equal `D.head_sha`. Otherwise `DISPUTE_RULING_CLAIM_INVALID`.
    - `claim_sha256`: the SHA-256 of the claim's canonical JSON (sorted keys, `(',', ':')` separators), as in §2.6 item 2.

    Every later part takes `D` and `claim_sha256` from here, never from model text, a comment or the PM.

    **12b. Freshness check F.** `_ruling_current(store, task_id, *, identity=D, arbiter_claim, claim_sha256, current_head)` is one function in `task_submission_cli.py`. It writes nothing. It returns the fresh record or refuses with one code. It runs these checks in this order:
    1. **Fresh record.** `record = store.get(task_id)`. No record object from an earlier read is reused. The record's `record_sha256` is the one any later CAS of this round expects.
    2. **Fresh head.** A live PR read through `_dispute_head` (`task_submission_cli.py:2515-2528`), run after the reload. A closed PR or an unreadable head refuses with its existing code. A head other than `D.head_sha` refuses `DISPUTE_STALE`.
    3. **Dispute state.** In the fresh record, exactly one `review_disputes` entry matches all four values of `D`.
       - That entry must be `OPEN`, with the same `arbiter_claim`. `SUPERSEDED` refuses `DISPUTE_SUPERSEDED`. A missing, duplicate or malformed entry, or a malformed list, refuses `DISPUTE_NOT_OPEN`.
       - `RULED` with this `arbiter_claim`, this `claim_sha256` and an `arbitrate` ruling returns that recorded result, with no write and no post (an earlier call won). Any other `RULED` state refuses `DISPUTE_ALREADY_RULED`. So does any other entry for (pr, finding_id) that is `RULED` (as `dispute_rule` refuses today, `:2820-2823`).
    4. **Arbiter claim.** The fresh record still holds the claim named by `arbiter_claim`. It must be `COMPLETED` with `verdict: 'RULING'`, and its recomputed canonical digest must equal `claim_sha256`. Otherwise `DISPUTE_RULING_CLAIM_INVALID`.
    5. **Newer formal outcome (check N).** On the fresh record's `review_dispatch_claim` and `completion_events`, the disputed verdict must still be the newest formal outcome at `D.head_sha`:
       - The disputed claim must still exist: the formal claim of `D.pr` at `D.head_sha` with `version == D.review_version`, still `COMPLETED` with verdict `FAIL` or `FIX`. Otherwise `DISPUTE_NO_FAIL_VERDICT`.
       - Any of the following refuses `DISPUTE_VERDICT_NEWER`:
         - (a) a formal claim (no `purpose`) of `D.pr` at `D.head_sha` with `version > D.review_version` that is `COMPLETED`, whatever its verdict;
         - (b) a formal Claude claim of `D.pr` at `D.head_sha` with `version > D.review_version` that is `DISPATCHED` and has a matching `REVIEWER` completion event (same `source_sha` and `version`). This is the **event-backed outcome**: a formal Claude claim never leaves `DISPATCHED`, and its verdict lives only in that event, as the merge gate reads it (`_merge_pass_claim`, `task_advance.py:1176-1189`; `_review_outcomes`, `task_advance.py:2607-2658`);
         - (c) any other formal claim of `D.pr` at `D.head_sha` with `version > D.review_version`, in any state (`CLAIMED`, `DISPATCHED` with no event yet, `UNKNOWN`). Its outcome may still arrive, so it counts as newer. This also covers a newer formal verdict comment that is on GitHub before its outcome is recorded, because `dispatch_review` stores the `CLAIMED` claim (`review_dispatch.py:1321-1332`) before it creates the D6 or starts any session (`:1334-1347`);
         - (d) a claim of `D.pr` at `D.head_sha` whose `version` is not an int, as the merge gate treats one (`task_advance.py:1204-1206`);
         - (e) a `REVIEWER` completion event with `source_sha == D.head_sha` and `version > D.review_version`, whether or not a claim matches it (as `task_advance.py:1210-1213`). An event at the head whose `version` is not an int also counts (fail-closed). So does an event at the arbiter claim's own version: A1c makes such an event impossible, so one that exists is treated as anomalous;
         - (f) a `review_dispatch_claim` or `completion_events` value that is not a list (or, for claims, a dict), or a list item that is not a dict. This is fail-closed, as `dispute_open_on` treats a malformed list (`review_repair.py:74-76`).
       - Only `purpose: 'dispute_ruling'` claims are left out of N. Each must be a well-formed ruling claim that the A1 store validated against its own entry.

    F runs at three points:
    - **F1, before posting.** It runs on every call that reaches step 12, first or replay, before `_ensure_verdict_posted` is called. A refusal posts nothing and writes nothing.
    - **F2, at the post fence.** It runs inside the post lock, as the `allowed` callback of `_ensure_verdict_posted`. `_post_under_lock` calls that callback after listing and matching the comments and before it re-reads its own receipt and posts (`review_dispatch.py:1175-1185`). The callback runs the policy gate as `dispatch_review`'s does (`:1250`), then F. It keeps F's refusal code in the call. If F refuses, the receipt records `FAILED` with `VERDICT_POST_NOT_ALLOWED` (`:1175-1176`), nothing is posted, and `arbitrate` reports F's own code, not the generic post code. F2 covers the time spent waiting for the lock and listing comments.
    - **F3, before the final CAS** (12e).

    **12c. Post.** llmtool posts the ruling comment itself through `_ensure_verdict_posted` (`review_dispatch.py:1077-1094`), under the existing post receipt and lock.
    - The receipt name is already per claim: `_verdict_post_name` is `{task_id}-{reviewer_task}` (`review_dispatch.py:868-875`), and the arbiter claim has its own `reviewer_task`.
    - **The receipt carries D.** For a `purpose: 'dispute_ruling'` claim, `_receipt_identity` (`review_dispatch.py:878-884`) adds `dispute: {pr, head_sha, review_version, finding_id}` from the claim's own `dispute`. `_check_receipt` already compares every identity key (`:959-967`), so a receipt for another identity refuses `VERDICT_POST_RECEIPT_UNAVAILABLE`. Formal receipts keep today's identity.
    - The caller-posting hand-off (`_reserve_caller_post`, `:1049-1074`) is not used, so the PM never receives ruling text to post.
    - **Comment matching must change for ruling claims.** A per-claim receipt does not change how comments are matched:
      - The ruling and the disputed FAIL carry the same binding line, because both bind the same Task, run, contract and head (`_verdict_binding_line`, `review_dispatch.py:997-1015`).
      - `_find_posted_verdict` (`:1018-1041`) treats any comment by the posting login that contains the binding line, but has a different body, as a conflict. All agents share one login (#926), so the disputed FAIL comment is always such a comment.
      - `_post_under_lock` then refuses `VERDICT_POST_CONFLICT` (`:1173-1174`) when no exact ruling exists. With the unchanged matcher, the ruling could never be posted.
    - So `_post_under_lock` chooses the matcher from the claim's own recorded `purpose`, at both places it matches: the `POSTED`-receipt branch (`:1120-1122`) and the fresh listing (`:1163`). The caller gives no matcher argument, and `purpose` is written by llmtool in step 7.
      - Formal claims keep `_find_posted_verdict` unchanged.
      - A `purpose: 'dispute_ruling'` claim uses a new `_find_posted_ruling(comments, login, text, binding_line, *, finding_id, pr, head, review_version)`. All four identity values come from the claim's `dispute`, never from model text.
    - `_find_posted_ruling` first selects the **bound ruling-shaped** comments. Each of these:
      - is by the posting login;
      - carries the binding line as a whole line;
      - has a `dispute_ruling` marker line or any `Ruling: ` line.

      A bound comment whose first non-empty line is a `review_verdict` marker and which has no `dispute_ruling` marker line is a verdict comment, not ruling-shaped, even if its free text holds a `Ruling: ` line. `parse_ruling` refuses any text with a `review_verdict` marker (§2.3), so such a comment can never be a ruling.
    - Each selected comment then falls into one of three cases. A `Ruling:` line parses if it matches the versioned header of §2.3 or the legacy header without `Version:` (`_RULING_HEADER`, `task_submission_cli.py:2578-2580`).
      - **Another identity.** It has exactly one `Ruling:` line, that line parses, and it names another finding, PR or head, or (versioned header only) another review version. It is outside the identity and is ignored.
      - **This identity.** It has exactly one `Ruling:` line, that line parses, and it names this finding, PR and head, and either this review version or, in the legacy header, no version. It is in the **ruling identity**. A legacy header for this finding, PR and head cannot show it is about another verdict, so it stays in (fail-closed).
      - **Unclear.** Its `Ruling:` line cannot be parsed, it has none, or it has more than one. It counts as a conflict (fail-closed).

      Within the ruling identity, `_find_posted_verdict`'s duplicate and conflict checks stay as they are:
      - a body byte-equal to `text` is the match;
      - a second exact match is `VERDICT_POST_DUPLICATE`;
      - a different body is `VERDICT_POST_CONFLICT`.
    - Bound comments outside the identity are neither a match nor a conflict for the ruling post. These are the disputed FAIL, any other `review_verdict` comment, and rulings on other findings or other review versions.
    - Formal posting is unchanged. `_find_posted_verdict` still treats a ruling comment at the head as a conflict. Today, a later formal verdict at the same head already conflicts with the disputed FAIL comment, so the ruling comment adds no new refusal case.

    **12d. Post result.**
    - The evidence comment id is the one llmtool's own receipt records, not a URL from the PM. Only a post result of `POSTED` with `verified: True`, or `ALREADY_POSTED`, that carries an integer `comment_id` (`review_dispatch.py:1133-1134`, `:1166-1172`, `:1195-1199`) counts.
    - `IN_PROGRESS`, `POSTED_UNVERIFIED`, a result without a comment id, or any other post failure (`VERDICT_POST_*`) reports `ARBITER_POST_PENDING` and writes no ruling. The dispute stays `OPEN`, and a later `arbitrate` replays step 12 under the same receipt (§2.4). It never posts a second time.
    - `VERDICT_POST_NOT_ALLOWED` raised because F2 refused reports F2's code (`DISPUTE_STALE`, `DISPUTE_VERDICT_NEWER`, …) and writes no ruling.
    - `VERDICT_POST_CONFLICT` or `VERDICT_POST_DUPLICATE` within the ruling identity reports `DISPUTE_RULING_POST_CONFLICT` and writes no ruling. The dispute stays `OPEN`, and the PM reports it to the Owner. A replay re-lists the comments and refuses again while the conflict remains. It never posts over a conflict.
    - llmtool builds `evidence_ref` itself as the PR comment URL of the trusted slug (`_dispute_repository`, `task_submission_cli.py:2468-2481`), the PR and that comment id. The URL must pass `valid_review_dispute_ref`, as at `:2491`. The existing refusal of an `evidence_ref` equal to the entry's own (`:2831-2832`) stays.

    **12e. Final check and CAS.** Each round of this part works on one fresh record:
    1. **F3.** Run F (12b). This reloads the record and reads the live head again. Any refusal writes nothing.
    2. **Verify on that record.** Run `_dispute_rule_verified(...)` on the record F3 returned. It is one internal function factored out of `dispute_rule`: freshness (`:2842-2852`), `_dispute_arbiter_registration`, `_dispute_ruling_claim` and `_dispute_ruling_comment`. The checks stay as they are, with these additions and changes:
       - **Addition:** the claim found must be the entry's own `arbiter_claim` (same `reviewer_task` and `version`) with `purpose: 'dispute_ruling'`.
       - **Change, for `purpose: 'dispute_ruling'` claims only:** the Reviewer check at `:2638` reads the claim's recorded `reviewer` instead of `claim_reviewer(claim)`. It still requires that key to equal `arbiter['reviewer']`, and the ruling marker's actor (`Claude`/`Codex`) must map to the same key. Formal claims keep `claim_reviewer` unchanged. This is not a weaker check: the recorded `reviewer` is written by llmtool in step 7, never by the PM or the model, and the model's own actor must still agree.
       - **Change, `arbitrate` only:** `_dispute_ruling_comment` (`:2649-2706`) is given `review_version = D.review_version`. It then requires the versioned header of §2.3 and compares `(finding_id, outcome, pr, review_version, head)`. The legacy `rule` path passes no version and keeps today's header check until it is retired (PR A3b).
       - The argument checks that `dispute_rule` applies today (`D6_TASK_ID` for the Reviewer Task and `valid_review_dispute_session` for the session, `:2815-2818`) also apply to the values from llmtool's own records. `'reviewer-' + 20 hex` matches `D6_TASK_ID` (`task_store.py:58`). `valid_review_dispute_session` (`task_store.py:1470-1474`) accepts 1-256 printable ASCII characters other than space that pass `_emitted_sensitive`. A Codex `thread_id` (16-64 hex or `-`, `review_dispatch.py:334`) and a Claude UUID session (`review_transcript.py:12`, C1) fit that shape. Whether `_emitted_sensitive` (in `test_output_excerpts.py`, out of scope) passes them is UNKNOWN. PR A3 pins it by test with fixed placeholder ids. If it refuses one, the ruling fails closed, and any change is its own reviewed PR that never removes the check (§8).
       - It passes `reviewer`, `reviewer_task`, `reviewer_session`, `outcome` and `evidence_ref` from its own records only. `outcome` is the one `parse_ruling` read from the arbiter's text (§2.3).
    3. **CAS.** Build the `RULED` entry with the `arbitrate` key set of §2.6 item 2 (including `finding_id` and `review_version` from `D`). Write it with one `compare_and_update` whose expected `record_sha256` is the one F3 read in this round. No other expected value is used.
    4. **CAS loser.** If the store refuses the write because `record_sha256` changed, the call does not retry the same write. It starts a new round at item 1: reload, read the head, run F and the verification again, and build a new write. Rounds are bounded to three. A fourth conflict reports `DISPUTE_RULE_CAS_CONFLICT` and writes nothing; a later `arbitrate` replays step 12, finds the `POSTED` receipt (`ALREADY_POSTED`, no second post) and goes on to 12e. The exception type the store raises for a changed `record_sha256` is in `task_store.py` (out of scope here, UNKNOWN); A3 pins it with the real `TaskStore`. Any other store exception reports `DISPUTE_RULE_WRITE_UNKNOWN`, and the next replay's F3 finds whether `RULED` was written.

    The CAS is what makes "the disputed verdict is still the newest formal outcome" hold at the moment `RULED` is written. Every newer formal claim and every `REVIEWER` completion event is stored in the same Task record, so any of them written after F3's reload changes `record_sha256` and makes the CAS lose. The loser's next round sees it in check N and refuses `DISPUTE_VERDICT_NEWER`.

    **12f. What a refusal leaves.**
    - A refusal before 12c posts nothing and writes nothing.
    - A refusal after the comment is posted (F3, verification or the CAS) writes no ruling. The dispute stays `OPEN` and keeps blocking. The comment stays on the PR, and the receipt stays `POSTED` with its comment id. That comment is never evidence on its own: `RULED` exists only with the record-level binding of §2.6, and `withdrawn_findings` reads only `RULED` entries. A later `arbitrate` runs F1 first and refuses again while the cause remains. It never posts a second time.
    - **A newer verdict during posting prevents `RULED`.** If a newer formal claim or `REVIEWER` event is stored while the post is in flight, F3 refuses `DISPUTE_VERDICT_NEWER`. If it is stored after F3's reload and before the CAS, the CAS loses and the next round refuses. So no `RULED` entry is ever written over a record that holds a newer formal outcome at the disputed head.
    - **Residual: the live head.** The PR head is not in the record, so a head move after F3's live read and before the CAS is not seen by the CAS. The `RULED` entry then binds the old head and `D.review_version` only. A ruling affects only that head and version (§4 item 6), and the formal PASS is still required on the new head (§4 item 8). So the residual widens nothing.

### 2.3 Ruling output contract

The arbiter's text must have these first three non-empty lines. This is the shape `_dispute_ruling_comment` requires today (`task_submission_cli.py:2656-2660`), plus the disputed review version in the header, so the text carries all of `D` (§2.2 step 3):

```
<!-- activity: type=dispute_ruling actor=<Claude|Codex> target=PM status=<UPHELD|WITHDRAWN> -->
Ruling: <F#> <UPHELD|WITHDRAWN> | PR: #<pr> | Version: <review_version> | HEAD: <head>
LLMTOOL-REVIEW-BINDING v1 task=<task> run=<run> contract=<digest> head=<head>
```

`<review_version>` is the disputed formal verdict's version (`D.review_version`), not the arbiter claim's version. Free-text reasons may follow the third line. The text is at most 65536 UTF-8 bytes, which is the store's `review_text` limit (`task_store.py:2092-2093`).

**`parse_ruling`.** One strict parser, `dispute_arbiter.parse_ruling(text, *, actor, finding_id, pr, review_version, head, binding_line)`, checks the ruling text for both routes. It reuses `_RULING_MARKER` and `_RULING_ANY_LINE` (`task_submission_cli.py:2575-2581`). It uses a new versioned header pattern, `_RULING_HEADER_V`, defined in `dispute_arbiter.py`; the version is `[1-9][0-9]{0,3}`, the range the formal readers accept (`review_repair.py:340-341`). The legacy `_RULING_HEADER` stays unchanged for the retired path and for the comment matcher (§2.2 step 12c). It returns the outcome or refuses `DISPUTE_RULING_OUTPUT_INVALID`. It refuses when:

- the first three non-empty lines are not exactly marker, versioned `Ruling:` header and binding line (a legacy header without `Version:` is refused);
- there is not exactly one `Ruling:` line or exactly one `dispute_ruling` marker line;
- the marker `status` and the header outcome differ, or either is outside `UPHELD`/`WITHDRAWN`;
- the marker actor is not `actor`, the chosen arbiter's family (`Claude` for `claude-reviewer`, `Codex` for `codex-reviewer`);
- the finding, PR, review version or head differs from the claim's `dispute.{finding_id, pr, review_version}` and `head_sha`, or the binding line is not exactly `binding_line`;
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

`arbitrate` on an entry that already has `arbiter_claim` does not start a new run. It finds the entry by `(pr, finding_id)` and the live head, as on the first call (§2.2 step 2). It then takes the arbiter claim named by the entry's `arbiter_claim`, never another claim of the same version or head. From then on it uses the entry's full `D` (§2.2 step 3) and follows the claim's state, like the prior-claim branch of `dispatch_review` (`review_dispatch.py:1231-1262`).

**Two kinds of write.** A replay can make two kinds of write, and they are gated differently:

- **Claim-truth writes** record what the runtime did: `CLAIMED` → `UNKNOWN`, and the step 11c collection `DISPATCHED` → `COMPLETED`. They are never rulings, so a newer formal verdict does not stop them. They still need the head check of their own step: C3 binds the Reviewer D6 to `D.head_sha`.
- **The ruling write** is `OPEN` → `RULED`. It happens only through step 12, after F1, F2 and F3 (§2.2 step 12b) on fresh reads.

So the replay never stops recording a true runtime fact, and it never rules on a record it has not freshly re-read.

| Entry / arbiter claim state | Result |
|---|---|
| entry `RULED` by this `arbiter_claim` | returns the recorded result; no read of the runtime, no post, no write. A newer formal verdict after `RULED` changes nothing here (`RULED` is terminal); its effect is limited by the version scope of §4 item 6 |
| entry `SUPERSEDED` | refuses `DISPUTE_SUPERSEDED`; no write |
| `CLAIMED` | becomes `UNKNOWN` (outcome uncertain; never replayed); reports `ARBITER_OUTCOME_UNKNOWN` |
| `DISPATCHED` | Claude only (a Codex claim never rests here): step 11c collection. A refusal there (`ARBITER_PENDING`, `ARBITER_SESSION_UNKNOWN`, `ARBITER_TRANSCRIPT_UNAVAILABLE`, `ARBITER_RULING_MISSING`/`_AMBIGUOUS`, …) writes nothing. Success writes `COMPLETED` once (claim-truth write) and then goes on to step 12, which starts with F1 |
| `COMPLETED`, `RULING`, entry `OPEN` | step 12: 12a identity, F1, post once under the receipt with F2 at the fence, then F3 and the CAS. Codes below |
| `COMPLETED`, `BLOCKED` | reports `DISPUTE_RULING_WITHHELD`; dispute stays `OPEN`; nothing posted |
| `UNKNOWN` | reports `ARBITER_OUTCOME_UNKNOWN`; dispute stays `OPEN` |

Step 12 on a replay ends in exactly one of these. None of the refusals writes anything.

| Step 12 outcome | Code | Post | Write |
|---|---|---|---|
| F1 or F2 finds the head moved | `DISPUTE_STALE` | none | none |
| F1 or F2 finds a newer formal outcome (check N, cases a-f) | `DISPUTE_VERDICT_NEWER` | none | none |
| F1 or F2 finds the disputed claim gone or no longer `FAIL`/`FIX` | `DISPUTE_NO_FAIL_VERDICT` | none | none |
| F1 or F2 finds the entry `SUPERSEDED`, or `RULED` by something else | `DISPUTE_SUPERSEDED` / `DISPUTE_ALREADY_RULED` | none | none |
| F1, F2 or F3 finds the arbiter claim changed | `DISPUTE_RULING_CLAIM_INVALID` | none | none |
| no usable comment id | `ARBITER_POST_PENDING` | at most once, under the receipt | none |
| conflict or duplicate in the ruling identity | `DISPUTE_RULING_POST_CONFLICT` | none | none |
| F3 refuses after the post (any F code) | F's code | already done; never repeated | none |
| CAS lost three times | `DISPUTE_RULE_CAS_CONFLICT` | already done; never repeated | none |
| F3 finds `RULED` by this `arbiter_claim` (another call won) | the recorded result | none | none |
| F3 passes and the CAS wins | `RULED` | done once | one CAS |

- **The order on every replay that reaches step 12 is fixed:** F1 → post (F2 inside the lock) → F3 → CAS. A replay after a post that did not rule starts again at F1. Because the receipt is `POSTED`, it never posts again.
- **Each check reads fresh.** F1, F2 and every F3 round call `store.get` and the live head reader again. A value from the call's first read, or from an earlier round, is never reused as proof that the verdict is still current. Only `D` and `claim_sha256` (§2.2 step 12a) carry over, and F3 re-checks both against the fresh record.
- A dispute whose arbiter claim is `UNKNOWN` or `BLOCKED` stays `OPEN` and keeps blocking.
- A dispute refused `DISPUTE_VERDICT_NEWER` also stays `OPEN` and keeps blocking at its head (`dispute_open_on`, `review_repair.py:72-80`). That is fail-closed. Its exits are a `supersede` by a replacement Task with a new head (§3), or an Owner decision relayed by the PM. Whether a `COMPLETED` arbiter claim refused `DISPUTE_VERDICT_NEWER` counts as a pending ruling for supersede (§3.2 item 2) is an open point (§8).
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
- `finding_id`, `review_version`: the disputed-verdict identity `D` (§2.2 step 3), so the ruling itself names which verdict it rules on;
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

  Both sets keep today's checks (`:1480-1488`) and are strictly typed: digests are 64 hex, and `comment_id`, `arbiter_claim_version` and `review_version` are positive non-bool ints.
- **Record-level binding, `arbitrate` set only.** `_validate` checks the whole record, so a `RULED` `arbitrate` entry is valid only if the same record holds the entry's own arbiter claim, and that claim:
  - is `COMPLETED` with `review_result.verdict == 'RULING'`;
  - has `dispute.{pr, head_sha, review_version, finding_id}` equal to the entry's `pr`, `head_sha`, `review_version` and `finding_id`, and to `ruling.head_sha`, `ruling.review_version` and `ruling.finding_id`;
  - has `version == arbiter_claim_version == entry.arbiter_claim.version` and `reviewer_task == ruling.reviewer_task == entry.arbiter_claim.reviewer_task`;
  - has `native_session_id == ruling.reviewer_session` and `reviewer == ruling.reviewer`;
  - has `review_result.outcome == ruling.outcome` and `review_result.source` equal to (`source_kind`, `output_sha256`);
  - matches the three digests: `sha256(review_text) == review_text_sha256`, `rules_sha256` equal, and the recomputed `claim_sha256` equal;
  - has `contract_digest` and `run_id` equal to the record's.

  So a `RULED` write cannot exist without a runtime-sourced `COMPLETED` ruling claim in the same record.
- **Transition (new writes).** In A1, `_check_review_disputes_transition` (`:1538-1560`) accepts `OPEN` → `RULED` with either set. The manual `rule` command and its existing tests still write the legacy set until A3b. A3b retires that writer with its own code before any store read (§2.5). After A3b merges, **PR A4** makes the transition accept a new `RULED` write only with the `arbitrate` key set. A new legacy-set write is then refused `REVIEW_DISPUTE_TRANSITION_INVALID`, while stored legacy entries still validate. A4 changes only `task_store.py` and its test.

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

Source read for §3, §7 and §8 (O7 child 3/3): `8c4eac1f`. `task_submission_cli.py`, `review_repair.py`, `high_debug.py` and `lineage_counts.py` were read at this commit. Every line cited from these four files in §3, §7 and §8 refers to it. Lines cited from other files keep the commits stated at the top of this note. `task_store.py`, `pm_caller.py` and the five status and event readers of §7.2 were not read for this child.

### 3.1 Interface

```
llmtool task dispute supersede <old_task_id> --pr <N> --finding <F#> --head <sha> --by <replacement_task_id> [--pm-session <id>]
```

- `--head` selects the entry. One (pr, finding) can have `OPEN` entries on several heads, because `_dispute_append` refuses a second `OPEN` only on the same head (`task_submission_cli.py:2762-2764`). llmtool matches the value against the stored entry. It only selects the entry and is never evidence.
- Argument checks, before any read: `--pr` and `--finding` are checked as in `_dispute_arguments` (`:2487-2489`); `--head` must be 40 lower-case hex; `<old_task_id>` and `--by` must be 32 lower-case hex and differ. Any failure refuses `DISPUTE_ARGUMENT_INVALID`.
- **Who may call:** a PM session that `pm_caller.check_caller` accepts for **both** the old Task and the replacement Task. The adapter calls it twice, in the same way `run_task_dispute` calls it once (`task_submission_cli.py:2887-2891`).
- A supersede carries no ruling, outcome, verdict, free text or URL, so it does not engage Owner ruling C. It still records the PM caller as actor.
- llmtool never supersedes automatically. A replacement Task may exist without its PM wanting the old dispute closed.
- `supersede` has its own subparser. The shared loop that adds a required `--evidence-ref` to every action (`:1038-1050`) does not apply. The adapter does not read `args.evidence_ref`, which today is read for every action (`:2893-2894`).
- A refusal prints only its code and exits 1, as `run_task_dispute` does (`:2906-2912`).

### 3.2 Conditions

One supersede runs one or more **rounds** under one chain lock. Each round re-reads both Tasks and runs every check below on that read. Any refusal writes nothing.

1. **Chain lock.** From a first read of the old Task, `key = lineage_counts.chain_key(store, old)`. Then `lineage_counts.acquire_chain_lock(store, key)`, as review repair does (`review_repair.py:799-808`) and as the High Debug reservation does (`high_debug.py:1397-1406`). If the lock cannot be taken, the call refuses `DISPUTE_CHAIN_LOCK_UNAVAILABLE`. The lock is released on every path.
2. **Old Task.** `_dispute_task(store, old)` (`task_submission_cli.py:2449-2460`) checks the ID shape, that the record is readable, that it is not abandoned (`DISPUTE_TASK_ABANDONED`, `:2455-2456`), and that `review_disputes` is a well-formed list (`DISPUTE_RECORD_INVALID`, `:2457-2459`). On this fresh record, `chain_key` must still equal `key` (as at `review_repair.py:806-808`). Otherwise the call refuses `DISPUTE_CHAIN_UNKNOWN`.
3. **Old entry.** Take the old Task's entries with this `pr`, this `finding_id` and `head_sha == --head`:
   - none, or one that is `DEFERRED`: `DISPUTE_NOT_OPEN` (deferral is already non-blocking history);
   - more than one: `DISPUTE_RECORD_INVALID`;
   - `RULED`: `DISPUTE_ALREADY_RULED`;
   - `SUPERSEDED`: the round runs items 5 and 6 and computes the §3.3 digest from them and from the stored `arbiter` snapshot, which cannot change after the supersede (§3.5). If the digest equals the stored `evidence_sha256`, return the recorded result and write nothing. Any other digest refuses `DISPUTE_ALREADY_SUPERSEDED`. A refusal in item 5 or 6 is returned as it is;
   - `OPEN`: go on.
4. **Arbiter claim.** An entry without `arbiter_claim` goes on. Otherwise, the claim it names (same `reviewer_task` and `version`, `purpose: 'dispute_ruling'`) must occur exactly once in `review_dispatch_claim`, or the call refuses `DISPUTE_RULING_CLAIM_INVALID`. Then the claim's state decides:

   | Arbiter claim | Supersede |
   |---|---|
   | `CLAIMED` | Refuses `DISPUTE_ARBITER_IN_FLIGHT`. A launch may be running, and its post-launch write must land on an `OPEN` entry (§3.4). If a crash leaves the claim `CLAIMED`, the next `arbitrate` replay makes it `UNKNOWN` (§2.4), and supersede can then go on. |
   | `DISPATCHED` (Claude) | Goes on. Its transcript is never collected after the supersede (§3.4). |
   | `UNKNOWN` | Goes on. |
   | `COMPLETED`, `BLOCKED` (withheld) | Goes on. There is no ruling to record. |
   | `COMPLETED`, `RULING` | **B1:** refuses `DISPUTE_RULING_PENDING`. **B2** (after A3): the moot rule of §3.4 decides. |

5. **Replacement link.**
   - The replacement record must be readable, have the `--by` value as its `task_id`, and not be abandoned. Its `continues` must equal the old Task ID exactly (direct successor only). Otherwise the call refuses `DISPUTE_REPLACEMENT_INVALID`.
   - Its `task_binding.contract_digest` must be a non-empty string that differs from the old Task's, or the call refuses `DISPUTE_REPLACEMENT_INVALID`. This check is weak on its own: `continues` is part of the contract digest (`task_submission_cli.py:539-542`), so a successor's digest always differs. The real proof that the dispute is moot is the new head (item 6).
   - `chain_key(store, replacement)` must equal `key`.
   - `lineage_counts.lineage_counts(store, replacement, base_family='pr-<N>', include_successors=True)` must return known values. `chain` must be a list whose first two IDs are the replacement and the old Task. `review_repairs`, `review_heads` and `test_fails` must not be `UNKNOWN`. Any `UNKNOWN` refuses `DISPUTE_CHAIN_UNKNOWN`.
     - The function returns `UNKNOWN` for an unreadable or malformed link, a cycle, a fork anywhere in the chain, a chain longer than `MAX_CHAIN`, or a malformed `review_repair_claims` list (`lineage_counts.py:9-13`, `:175-185`, `:191-193`).
     - Supersede reads these counts only to prove the chain. It never writes or corrects one.
6. **New head.**
   - Read the replacement's Executor D6 the way `_dispute_arbiter_registration` reads a D6 (`task_submission_cli.py:2539-2551`): `completion.executor_task_id`, `completion.evidence.common_dir`, `read_d6_registration(..., allowed_states=_D6_EVIDENCE_STATES)` and `collect_git_snapshot`. Its `result_sha` must be 40 hex and equal the snapshot head. The worktree must be clean, as `_preflight` requires (`review_dispatch.py:245-256`, read at `c1ab1339`).
   - That head must differ from the entry's `head_sha`, from every `head_sha` in the old Task's `review_dispatch_claim`, and from every `head_sha` in its `review_disputes`.
   - Otherwise the call refuses `DISPUTE_REPLACEMENT_HEAD_INVALID`.
7. **Write** (§3.3).

### 3.3 Write

One `compare_and_update` on the **old** Task, with `expected={'record_sha256': ...}` taken from this round's item 2 read. This is the same call shape as at `task_submission_cli.py:2768-2769` and `:2869-2870`. It changes only `review_disputes`, and within it only the one entry:

- `state: 'SUPERSEDED'`.
- `superseded = {by_task, by_run, by_contract_digest, new_head, old_head, old_contract_digest, arbiter, superseded_at, evidence_sha256}`:
  - `old_head` is the entry's `head_sha`. `new_head` is the head from item 6.
  - `arbiter` is `null` for an entry without `arbiter_claim`. Otherwise it is `{reviewer_task, version, state, claim_sha256, moot_code}`, a snapshot of the claim at this write:
    - `state` is the claim's state (`DISPATCHED`, `UNKNOWN` or `COMPLETED`);
    - `claim_sha256` is the SHA-256 of the claim's canonical JSON (sorted keys, `(',', ':')` separators, as in §2.6 item 2);
    - `moot_code` is `null`, except under the B2 moot rule.
  - `evidence_sha256` is the SHA-256 of the canonical JSON of every other `superseded` field except `superseded_at`.
- One history event: `{event: 'SUPERSEDED', at: superseded_at, actor: 'pm:<caller session>', head_sha: <old_head>, refs}`. `refs` holds `by_task`, plus `reviewer_task`, `version` and `claim_sha256` when `arbiter` is not `null`. All of these are keys of the fixed `refs` set (§2.6 item 3).

Every other key of the entry stays byte-equal, including `arbiter_claim`, `review_version`, `evidence_ref` and the earlier `history` events. No other field of the old record is written. **The replacement Task is never written.** It finds the old dispute through `continues`.

**CAS loser.** The store may refuse the write because `record_sha256` changed. The call then starts a new round at item 2, still under the lock: it re-reads both Tasks and runs every check again. So the loser of a race ends with the code that matches the winner's write (§3.4).
- Rounds are limited to three. A fourth conflict refuses `DISPUTE_SUPERSEDE_CAS_CONFLICT`.
- Any other store exception refuses `DISPUTE_SUPERSEDE_WRITE_UNKNOWN`. The next call's read shows whether the write landed. An identical replay then returns the recorded result.
- The exception type the store raises for a changed `record_sha256` is defined in `task_store.py`, which was not read here. B1 pins it with the real `TaskStore`, as A3 does for `RULED`.

### 3.4 Races with native completion and finalization

Every write of a dispute flow is a compare-and-update on the old Task's `record_sha256`:
- open and defer (`task_submission_cli.py:2768-2769`);
- the arbiter claim (§2.2 steps 7 and 11, and step 11c C8);
- `RULED` (§2.2 step 12e);
- supersede (§3.3).

The entry and the arbiter claim live in that one record. So two writes made from the same read never both land, and the loser re-reads. A1 also adds two store refusals as defence in depth:
- `OPEN` → `SUPERSEDED` while the entry's arbiter claim is `CLAIMED`;
- any change to a `purpose: 'dispute_ruling'` claim whose entry is `RULED` or `SUPERSEDED` (§7 PR A1).

| Concurrent write | Supersede commits first | The other write commits first |
|---|---|---|
| `arbitrate` step 7: append the `CLAIMED` arbiter claim | Step 7's CAS loses. `arbitrate` refuses before any D6 or launch (§5, row "Replay of `arbitrate`"). A replay refuses `DISPUTE_SUPERSEDED` at step 2 | Supersede's next round finds `CLAIMED` and refuses `DISPUTE_ARBITER_IN_FLIGHT` |
| Codex completion, step 11a (`CLAIMED` → `COMPLETED` or `UNKNOWN`) | Cannot happen. Supersede refuses while the claim is `CLAIMED`, and the A1 store refuses that write too | Supersede's next round decides by the new state (§3.2 item 4) |
| Claude launch, step 11b (`CLAIMED` → `DISPATCHED`) | Cannot happen, as for 11a | Supersede's next round goes on (`DISPATCHED`) |
| Claude collection, step 11c C8 (`DISPATCHED` → `COMPLETED`) | C8's CAS loses and writes nothing. Every later `arbitrate` refuses `DISPUTE_SUPERSEDED` at step 2, before any session or transcript read. The A1 store also refuses the transition. The claim stays `DISPATCHED` for good. A ruling in the transcript is never stored, posted or ruled | Supersede's next round finds `COMPLETED`. A `BLOCKED` result goes on. A `RULING` result is pending (B1) or goes to the moot rule (B2) |
| Finalization, step 12e (`OPEN` → `RULED`) | F2 or F3 refuses `DISPUTE_SUPERSEDED` (§2.2 step 12b check 3). A ruling comment that was already posted stays on the PR and rules nothing (§4 item 9) | Supersede's next round refuses `DISPUTE_ALREADY_RULED` |
| A second supersede of the same entry | Both run under the one chain lock, so they do not interleave. The second finds `SUPERSEDED`. With the same digest it returns the recorded result; otherwise it refuses `DISPUTE_ALREADY_SUPERSEDED` | (same) |
| `task abandon` of the old Task | The entry stays `SUPERSEDED`. B1 pins that the abandon write keeps `review_disputes` byte-equal | Supersede's next round refuses `DISPUTE_TASK_ABANDONED` (§3.5) |
| Review repair or High Debug on the old Task | Their counted writes take the same chain lock (`review_repair.py:799-808`, `high_debug.py:1397-1406`), so they run one after the other with supersede. Both are refused at the old head before and after the supersede (§3.5) | (same) |

`open` and `defer` of the same (pr, finding, head) do not race. They are refused `DISPUTE_ALREADY_OPEN` while the entry is `OPEN` (`:2762-2764`), and `DISPUTE_SUPERSEDED` after it (B1).

**The moot rule (B2, after A3).** A `COMPLETED` `RULING` arbiter claim on an `OPEN` entry is a ruling that `arbitrate` step 12 has not recorded yet. Supersede runs F's record checks on this round's read: `_ruling_check(record, identity=D, arbiter_claim, claim_sha256, current_head)`. These are §2.2 step 12b checks 2-5, which A3 factors out of `_ruling_current` (§7). The live head read of check 2 runs after this round's store read.

- **F passes:** the ruling can be recorded now. Supersede refuses `DISPUTE_RULING_PENDING`, and the PM runs `arbitrate` first. A ruling that has already been produced is never hidden by a supersede.
- **F refuses with a code of the moot set:**
  - `DISPUTE_STALE`: the PR head moved off `D.head_sha`;
  - `DISPUTE_PR_NOT_OPEN`: the PR is closed or merged (`_live_pr_head`, `task_submission_cli.py:2510-2511`);
  - `DISPUTE_VERDICT_NEWER`: a newer formal outcome exists at the head;
  - `DISPUTE_NO_FAIL_VERDICT`: the disputed verdict is gone.

  In each of these cases, `arbitrate` would refuse the same ruling now for the same reason. Supersede goes on and records the code as `superseded.arbiter.moot_code`.
- **F refuses with any other code** (`DISPUTE_HEAD_UNKNOWN`, `DISPUTE_RULING_CLAIM_INVALID`, `DISPUTE_NOT_OPEN`, …): supersede refuses with that code. A doubtful case is never treated as moot.

**Why the moot rule is safe:**
- Supersede lifts no refusal and withdraws no finding (§3.5, §3.6).
- The ruling text stays in its `COMPLETED` claim, and `superseded.arbiter.claim_sha256` binds it.
- So a moot supersede only gives up a `RULED` record that `arbitrate` would refuse at that moment.

This answers the open point of §2.4 (last bullets) and the old §8: a `COMPLETED` ruling that check N refuses is moot, not pending. B1 alone is complete and fail-closed. If the Owner, through the PM, declines the moot rule, B2 is not merged, and every `COMPLETED` `RULING` claim keeps blocking supersede.

### 3.5 Old head, history, abandoned records and counts

**Old-head blocking stays.**

- `review_repair.py` gains `dispute_block_on(task, pr, head)`. It returns `None`, `'OPEN'` or `'SUPERSEDED'` for that PR head of that Task.
  - A malformed list or item gives `'OPEN'`, as `dispute_open_on` treats it today (`review_repair.py:74-77`).
  - `'OPEN'` wins when both states exist at the head.
  - `dispute_open_on` (`:72-80`) keeps its name and returns `dispute_block_on(...) is not None`. Every caller keeps every refusal it has today, including callers outside the read scope, and `SUPERSEDED` now blocks too.
- Review repair (`review_repair.py:689-691`) refuses `REPAIR_DISPUTE_OPEN` for `'OPEN'`, as today, and `REPAIR_DISPUTE_SUPERSEDED` for `'SUPERSEDED'`. Test-failure repairs still skip the check (`:690`).
- High Debug (`high_debug.py:1537-1541`) refuses `DEBUG_DISPUTE_OPEN` or `DEBUG_DISPUTE_SUPERSEDED` at the same point: before any entry is written, so no Debug is used.
- `withdrawn_findings` (`review_repair.py:83-93`) reads only `RULED` entries, so a `SUPERSEDED` entry withdraws nothing.
- `open`, `defer`, `rule` (until A3b) and `arbitrate` refuse `DISPUTE_SUPERSEDED` for the same (pr, finding, head).
- The replacement Task is judged on its own record. `dispute_open_on` reads only the Task it is given (`review_repair.py:74`), and supersede copies nothing into the replacement. So the old dispute does not block repair or Debug at the new head. If a review of the new head repeats the finding, that is a new finding of a new verdict.

**Terminal history.**

- `SUPERSEDED` is terminal, like `RULED` (§4 item 12). The A1 store refuses every later change to the entry: its state, `superseded`, `arbiter_claim`, `history` or any other key.
- The arbiter claim stays byte-equal in whatever state it is in. The A1 store refuses any change to it once its entry is terminal. So a `DISPATCHED` Claude claim never becomes `COMPLETED`, and a `COMPLETED` claim keeps its text and `source`.
- The entry's `history` is the stored list plus exactly one `SUPERSEDED` event (§2.6 item 3).
- A ruling comment that llmtool already posted stays on the PR. Without `RULED` it rules nothing (§4 item 9).

**Abandoned records.** No dispute flow writes an abandoned Task. This needs no store exception, so the earlier open point about whether `task_store.py` allows a write on an abandoned record no longer matters.

- `_dispute_task` refuses `DISPUTE_TASK_ABANDONED` (`task_submission_cli.py:2455-2456`). Supersede calls it in every round (§3.2 item 2).
- **Old Task already abandoned:** supersede refuses. Its `OPEN` entry stays as the Task's terminal history. It blocks the old head exactly as `SUPERSEDED` would, because both make `dispute_open_on` true, so nothing is lost. The PM order is: supersede first, then abandon.
- **Abandon after supersede:** the entry stays `SUPERSEDED`.
- **Abandoned replacement:** the call refuses `DISPUTE_REPLACEMENT_INVALID`.
- **Arbiter writes:**
  - `arbitrate` refuses an abandoned Task at step 2 on every call, through `_dispute_task`.
  - A call that launched before the abandon makes its post-launch write (step 11a, 11b or the `UNKNOWN` exception path) on a fresh read. If that read shows the Task abandoned, the call writes nothing, the claim stays `CLAIMED`, and the call reports `DISPUTE_TASK_ABANDONED` (PR A2).
  - Step 11c C8 runs after step 2 in the same call. An abandon between them changes `record_sha256`, so the C8 CAS loses (PR A2b).
  - F's fresh-record check (§2.2 step 12b item 1) refuses an abandoned record with `DISPUTE_TASK_ABANDONED`, so no `RULED` is ever written on an abandoned Task (PR A3).

**Counts are kept and inherited.**

- Supersede writes only `review_disputes`. `lineage_counts.py` reads only these fields:
  - `review_repair_claims` (`:191-193`, `:241-246`);
  - `high_debug` (`:266-272`);
  - `continues` links (`:60-80`, `:83-127`);
  - completion result heads (`:572-607`).

  It never reads `review_disputes` or `review_dispatch_claim`. So supersede changes no count. An arbiter claim, in any state, is never counted as a review repair, a failed review head, a test failure or a Debug.
- The replacement inherits the old Task's counts through `continues`. `lineage_counts` (`:148-218`), `chain_review_repairs` (`:221-252`) and `chain_debug_used` (`:255-273`) sum every Task of the chain, so "a continuation never resets a limit" (`:6-7`).
  - `chain_records` and `successor_records` (`:60-127`) do not skip abandoned Tasks, so an abandoned old Task still counts.
  - Review repair reads these counts under the chain lock (`review_repair.py:799-825`), and so does the High Debug reservation (`high_debug.py:1397-1416`).
- If the chain is in doubt, every count is `UNKNOWN`, and `UNKNOWN` counts as the limit (`lineage_counts.py:9-13`; `_review_gate`, `review_repair.py:542-553`). Supersede refuses `DISPUTE_CHAIN_UNKNOWN` on such a chain (§3.2 items 2 and 5). It never writes onto a chain in doubt, and it never turns an `UNKNOWN` count into a known one.

### 3.6 What supersede never does

- **It never rules.** No `ruling` key is set, so `withdrawn_findings` ignores the entry and the finding is never treated as withdrawn.
- **It never lifts a refusal on the old head** (§3.5).
- **It never writes** the replacement Task, an abandoned Task, or any field of the old Task other than the one entry.
- **It never touches** a count, a `continues` link, a `review_dispatch_claim` or a `completion_events` entry.
- **It never runs the arbiter:** it never launches, stops or collects an arbiter run.
- **It never posts to GitHub.** Its only GitHub read is B2's live head read inside F.

### 3.7 Fail-closed cases

This list replaces the `supersede` part of §6. In every case nothing is written, and the dispute stays as it was.

- the caller does not own both Tasks;
- chain problems: the chain lock is unavailable, the chain key changed, or the chain or its counts are `UNKNOWN`;
- the old Task is abandoned, unreadable, or has a malformed dispute list;
- entry problems: there is no entry for (pr, finding, head), or more than one, or the one entry is `DEFERRED`, `RULED`, or `SUPERSEDED` with another digest;
- arbiter-claim problems:
  - the claim is missing, duplicated or malformed;
  - the claim is `CLAIMED`;
  - the claim is `COMPLETED` `RULING` (B1), or F accepts it or refuses it with a code outside the moot set (B2);
- the replacement is unreadable, abandoned, not the direct successor, unbound, or has the same contract digest;
- the replacement head is unregistered, dirty, unequal to its snapshot, or equal to an old head;
- three CAS conflicts in a row, or a store exception whose outcome is unknown.

---

## 4. Invariants

1. **No ruling without a verified arbiter run.** `RULED` is written only by `arbitrate` step 12. That step requires all of the following:
   - a `COMPLETED` `purpose: 'dispute_ruling'` claim created by llmtool for this entry, with the llmtool-recorded `reviewer` equal to the chosen arbiter and to the ruling marker's actor;
   - a registered Reviewer D6 that llmtool created at the disputed head;
   - a native session bound by `_bind_d6` from the runtime receipt;
   - a ruling comment llmtool posted itself whose body equals the claim text byte for byte;
   - every check of `_dispute_rule_verified`, run on the record of the last F3 round (§2.2 step 12e).
2. **The disputing PM cannot pass a session id or a verdict.** No command accepts a reviewer, Reviewer Task, session, outcome, ruling text or ruling evidence URL. `task dispute rule` is retired (§2.5).
3. **The PM cannot choose the arbiter.** `choose_arbiter` is pure and reads only records (`review_repair.py:143-167`). Policy is re-read before each side effect, including at the post fence (F2).
4. **Head binding.** The same head must hold at every step: live PR head, Executor D6 head, entry `head_sha`, Reviewer D6 base and `result_sha`, claim `head_sha`, `review_result.head`, the binding line and the `Ruling:` header. The live head is read again at F1, F2 and every F3 round. The one window the CAS cannot cover, a head move after F3's read, is bounded by item 6 (§2.2 step 12f).
5. **Identity binding.** Every record and text of the arbitration carries the whole disputed-verdict identity `D = (pr, head_sha, review_version, finding_id)`, and every check compares all four values:
   - the dispute entry (`pr`, `head_sha`, `review_version`, `finding_id`, stored at open, `task_submission_cli.py:2765-2767`);
   - the arbiter claim's `dispute` (§2.2 step 7), checked against the entry by the A1 store;
   - the ruling output: the versioned `Ruling:` header (§2.3), checked by `parse_ruling` on both routes;
   - the post receipt identity (§2.2 step 12c) and the ruling comment matcher's identity (`_find_posted_ruling`);
   - the `ruling` dict (`finding_id`, `review_version`, `head_sha`) and its record-level binding to the claim (§2.6 item 2);
   - the withdrawal lookup (item 6).

   No value of `D` is taken from model text, a comment or the PM after open. A mismatch anywhere refuses; it never falls back to a partial match.
6. **A ruling's effect is version-scoped.** A `WITHDRAWN` ruling withdraws its finding only from the one formal verdict it ruled on: `D.review_version` at `D.head_sha` on `D.pr`.
   - `withdrawn_findings(task, pr, head, version)` (`review_repair.py:83-93`) gains a `version` argument. It counts an entry only if the entry is `RULED`, its ruling is `WITHDRAWN`, its `pr` and `head_sha` match, `ruling.head_sha == head`, and the entry's `review_version` equals `version` (a non-bool int). For an `arbitrate` ruling, `ruling.review_version` must also equal `version`.
   - A non-int `version` withdraws nothing: every finding stays blocking (fail-closed).
   - Legacy `RULED` entries carry `review_version` on the entry (`task_submission_cli.py:2766`), so they are scoped the same way.
   - `_review_gate` (`review_repair.py:508-537`) passes the failed packet's `version` (set from the formal claim at `review_repair.py:372`) at its one call (`:531`). There is no other caller in `llmtool/`.
   - So a newer formal verdict at the same head, even one that reuses the same finding ID, is judged on its own findings. A ruling on version N never lowers the blocking findings of version N+1.
7. **A ruling is written only against the current verdict.** `RULED` is written only by a CAS whose expected `record_sha256` is that of a record in which F (§2.2 step 12b) has just passed:
   - the live head equals `D.head_sha`;
   - the entry is still `OPEN` with this `arbiter_claim`;
   - the arbiter claim is byte-equal to the one posted;
   - check N finds no newer formal outcome at the head.

   Check N covers `COMPLETED` claims, Claude `DISPATCHED` claims backed by a `REVIEWER` event, pending newer claims in any state, `REVIEWER` events with no claim, and malformed lists. Every one of these lives in the Task record, so a newer verdict stored at any time after F3's reload makes the CAS lose, and the loser re-evaluates on a fresh reload. A newer verdict before F1 or F2 prevents the post. One stored during or after the post, up to the CAS, prevents `RULED`.
8. **A ruling never replaces the formal PASS.** `formal_pass_required: True` stays (`task_submission_cli.py:2873`).
9. **A posted ruling comment rules nothing by itself.** A ruling comment without a `RULED` entry, for example one posted before a newer verdict refused F3, has no effect. `withdrawn_findings` reads only `RULED` entries, and a `RULED` `arbitrate` entry needs the record-level binding of §2.6. A replay never posts the same ruling twice; the per-claim receipt stays `POSTED`.
10. **Nothing resets.**
    - Counts, the `continues` lineage, `review_dispatch_claim` versions and the old head's refusals never reset or decrease.
    - A `purpose: 'dispute_ruling'` claim is never a formal verdict, in any state and with any text (withheld text included). Every formal reader excludes it by `purpose`, not only by text marker (`_is_ruling_claim`, `task_submission_cli.py:2585-2592`): `_failed_event` (`review_repair.py:336-344`), `_dispute_fail_verdict` (`task_submission_cli.py:2724-2729`), the disputed-claim lookup of `arbitrate` step 3, and `dispatch_review`'s prior-claim branch (`review_dispatch.py:1231-1262`). The readers in `review_result.py` and `review_transcript.py` are known and covered by PR A1c (§8).
    - The merge-gate readers in `task_advance.py`, read at `c1ab1339`, are `_merge_pass_claim` (`task_advance.py:1167-1214`) and `_review_outcomes` (`:2598-2659`). Both need a `REVIEWER` completion event at a claim's version before they take an outcome from it (`:1176-1181`; `:2616`, `:2645`, `:2656`). A1c makes such an event impossible for a ruling claim, so neither reads a ruling as an outcome. `_merge_pass_claim`'s newer-claim veto (`:1198-1206`) treats an arbiter claim as newer than any older PASS at that head. That is fail-closed, and a later formal review takes a version above it. PR A1b pins both by test. Other merge-gate code is still UNKNOWN; A1b greps every reader of `review_dispatch_claim`.
    - So after an `UPHELD` ruling, review repair of that head still finds the disputed FAIL verdict and its comment, as it would without the arbiter claim.
    - Whether lineage counting counts it as a review round is UNKNOWN here. PR A1b pins whichever it is by test, and it may only add to a count, never lower one.
11. **Every transition is audited** in the append-only entry `history` (§2.6). The store refuses rewriting it.
12. **Terminal states.** `RULED` and `SUPERSEDED` are terminal. No transition leaves them. A newer formal verdict after `RULED` does not reopen, rewrite or extend the ruling; item 6 limits its effect to `D.review_version`.

---

## 5. Threats

| Threat | Defence |
|---|---|
| **Forged session.** PM or another agent presents a session of its choosing. | No option accepts a session. The session comes only from the runtime receipt (`thread.started` / `actual_session_id`), and `_bind_d6` binds it. `_dispute_arbiter_registration` requires the registration's `native_session_id` to equal it (`:2561-2563`). Developer sessions and the first-review session are excluded (`:2564-2571`, `:2850`). |
| **Forged Reviewer D6.** A D6 created by hand at the head. | llmtool generates `reviewer_task` at claim time and records it on the entry before the D6 exists. Verification uses only that id; any other D6 is never consulted. |
| **Forged evidence comment.** All agents share one GitHub login (#926), so someone posts a byte-equal comment, or a different ruling for the same finding. | The comment id comes from llmtool's own post receipt, not a URL argument. Within the ruling identity (§2.2 step 12), `_find_posted_ruling` keeps the poster's duplicate and conflict checks (as in `_find_posted_verdict`, `review_dispatch.py:1018-1041`). A second exact copy, or a different ruling-shaped comment for the same finding, PR, head and review version (or a legacy header for the same finding, PR and head), refuses `DISPUTE_RULING_POST_CONFLICT`. `_dispute_ruling_comment` still requires an unedited comment posted after `opened_at`. |
| **Disputed FAIL blocks the ruling post.** The FAIL and the ruling share one binding line and one login. | The ruling matcher is chosen from the claim's recorded `purpose`, never from a caller argument. It ignores bound comments outside the ruling identity: the disputed FAIL, other verdicts, and rulings on other findings or review versions. Formal posting keeps `_find_posted_verdict` unchanged. |
| **Wrong head.** The PR moves between open, launch and ruling. | Live head checked at steps 2, 5 and 9, and at F1, F2 and every F3 round of step 12. A move refuses `DISPUTE_STALE`. The entry stays `OPEN` on the old head; the history keeps the arbiter claim. A move after F3's read binds the ruling to the old head and version only (§2.2 step 12f). |
| **Replay of an old ruling.** A ruling from another dispute, verdict version, PR, Task or run. | The ruling header binds finding, PR, disputed review version and head. The binding line binds task, run and contract. The claim binds `dispute.{pr, finding_id, head_sha, review_version}`, and the `ruling` dict and post receipt carry the same identity. One arbiter claim per entry; `RULED` is terminal. |
| **Replay of `arbitrate`.** It is called twice, or two calls run at once. | The second call follows the recorded claim (§2.4). A CAS conflict on `record_sha256` refuses the loser before any D6 or launch. In step 12 the post lock lets only one call post; a `RULED` CAS loser reloads, finds the winner's `RULED` entry for this `arbiter_claim` and returns it with no write. |
| **Race with a new formal review.** A new formal review takes the same version, or replays the arbiter claim. | Versions are allocated under the record CAS. `dispatch_review`'s prior-claim branch (`review_dispatch.py:1231-1262`) must refuse `REVIEW_DISPATCH_VERSION_USED` for a `purpose: 'dispute_ruling'` claim, so it never posts ruling text as a verdict. |
| **Newer verdict while the ruling is in flight.** A newer formal verdict lands before a replay, during the post, or between the final read and the CAS. A Claude formal review may show it only as a `DISPATCHED` claim plus a `REVIEWER` event. | Check N (§2.2 step 12b) counts every newer formal claim in any state, event-backed `DISPATCHED` outcomes, and `REVIEWER` events with no claim. It runs on a fresh reload at F1, F2 and F3. The CAS expects F3's `record_sha256`, so a verdict stored after F3 makes the CAS lose, and the loser's next round refuses `DISPUTE_VERDICT_NEWER`. No `RULED` is written; a posted comment rules nothing (§4 item 9). |
| **Newer verdict after `RULED`.** A newer formal verdict at the same head reuses the withdrawn finding ID. | `withdrawn_findings` is version-scoped (§4 item 6). The ruling applies only to `D.review_version`, so the newer verdict's findings all stay blocking. |
| **Race supersede vs arbitrate.** Both run at once. | Both write through CAS on the old Task. Whichever commits second re-reads and refuses (`DISPUTE_SUPERSEDED` or `DISPUTE_RULING_PENDING` / `DISPUTE_ALREADY_RULED`). |
| **Supersede used to escape a ruling.** | A pending `COMPLETED` ruling refuses supersede. Supersede lifts no refusal and withdraws no finding. |
| **Forged replacement.** A Task that does not really replace the old one. | The checks are: `continues` equals the old Task exactly, same chain key under the chain lock, a different contract digest, and a registered, clean Executor D6 with a new head. |
| **Prompt injection in the PM's dispute text.** | The text is screened, size-limited and fenced as untrusted data. The output contract is fixed and parsed strictly; anything else is `BLOCKED` and no ruling. |
| **Dispute text swapped after open.** Someone edits the PM's dispute comment, or posts one later and points at it. | The entry's `evidence_ref` is fixed at open. The comment must be unedited and created no later than `opened_at`. Its digest is bound into the arbiter claim before launch, and it is read once (§2.2 step 6). |
| **Arbiter claim read as a formal review.** The arbiter claim has the highest version at the head. | Every formal reader excludes `purpose: 'dispute_ruling'` (§4 item 10, PRs A1b and A1c). A2 does not merge before A1b and A1c, so no arbiter claim exists before the readers skip it. |
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
  - ruling output invalid or withheld, including a ruling header without the disputed review version or with another one; post receipt unavailable, in progress, unverified, without a comment id, or of another identity; a conflict or duplicate within the ruling identity, or an unparsable ruling-shaped bound comment; any verification refusal;
  - at F1, F2 or any F3 round: head moved or unreadable; entry no longer `OPEN` with this `arbiter_claim`; arbiter claim changed; disputed claim gone; a newer formal outcome (check N, cases a-f, including a Claude `DISPATCHED` event-backed outcome); `RULED` CAS lost three times; a store exception of unknown outcome;
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

### 7.1 Merge order

**A1 → R0 → R1…Rn → A1b → A1c → A1d → B1 → A2 → A2b → A3 → B2 → A3b → A4 → D1.**

Each PR may merge only after every PR in its "Needs" column has merged. The linear order above meets all of them.

| PR | Needs | Why |
|---|---|---|
| A1 | none | Adds every new store shape. Nothing writes these shapes yet. |
| R0 (docs) | none | Classifies the five readers of §7.2 that this note could not read. |
| R1…Rn | R0 | One PR (at most four files) for each reader that R0 classes as "change". There are none if R0 finds none. |
| A1b | A1 | Formal readers skip ruling claims before any ruling claim exists. |
| A1c | A1 | Result and transcript readers refuse ruling claims before any exists. |
| A1d | none | Withdrawal is version-scoped before any `arbitrate` ruling exists. |
| B1 | A1, R0, every R PR | B1 makes the first `SUPERSEDED` write, so every reader of dispute states must handle that state first. |
| A2 | A1, A1b, A1c, R0, every R PR | A2 stores the first arbiter claim, so no formal or status reader may still pick it. |
| A2b | A2 | Adds Claude collection to the A2 launch. |
| A3 | A1d, A2b | A3 writes the first `arbitrate` `RULED` entry and adds F. |
| B2 | A3, B1 | The moot rule calls F (`_ruling_check`). |
| A3b | A3 | Retires the manual `rule`, the last writer of the legacy key set. |
| A4 | A3b | The store refuses new legacy-set `RULED` writes. |
| D1 | A3b, B2 | Rule-file text describes the final commands. |

- A2 must not merge before A1b, A1c and the R gate. So no arbiter claim is ever stored while a formal or status reader could still pick it.
- B1 must not merge before the R gate. So no `SUPERSEDED` entry is ever stored while a reader could treat it as "no dispute".
- A3 must not merge before A1d. So no `arbitrate` `RULED` entry is ever written while `withdrawn_findings` is not version-scoped.
- A4 must not merge before A3b, which retires the last legacy writer.

Which PR carries each change:

| Change | PR |
|---|---|
| Claim keys `purpose`/`dispute`/`reviewer`; ruling `review_result` shape (`RULING`/`BLOCKED`, `outcome`, `source`) | A1 |
| `DISPATCHED` → `COMPLETED` for Claude ruling claims only; no #1636 settle for ruling claims; fixed keys extended | A1 |
| `RULED` ruling evidence keys (including `finding_id`, `review_version`) and record-level binding; `arbiter_claim`; `history` | A1 |
| `OPEN` → `SUPERSEDED` with `superseded` (§3.3); refused while the arbiter claim is `CLAIMED`; terminal entries and their ruling claims frozen (§3.4) | A1 |
| Classification of `board_check.py`, `task_status.py`, `chat_tui_status.py`, `chat_tui_events.py`, `github_events.py` (§7.2) | R0 |
| Any reader change that R0 finds | R1…Rn |
| Formal readers in `review_repair.py`, `review_dispatch.py`, `task_submission_cli.py` skip ruling claims, including `_claude_review_claim_matches` (§7.2); merge-gate readers in `task_advance.py` pinned | A1b |
| `record_review_result`, `_settle_candidate` and `unposted_verdict` refuse ruling claims | A1c |
| `withdrawn_findings` and `_review_gate` version-scoped (§4 item 6) | A1d |
| `supersede` with the B1 pending rule; `dispute_block_on`; `REPAIR_DISPUTE_SUPERSEDED`, `DEBUG_DISPUTE_SUPERSEDED`; `open`/`defer`/`rule` refuse `DISPUTE_SUPERSEDED` | B1 |
| `dispute_arbiter.py` (prompt, `parse_ruling` with the versioned header, `parse_codex_ruling`); `dispatch_arbiter`; `_dispatch_codex` `parser` argument; `arbitrate` steps 1-11a/11b, including check N at step 3; no post-launch write on an abandoned record (§3.5) | A2 |
| Claude collection, step 11c (`ruling_blocks`, `collect_claude_ruling`) | A2b |
| Step 12: identity, freshness check F (F1/F2/F3, as `_ruling_current` over `_ruling_check`, with the abandoned-record refusal), ruling receipt identity, `_find_posted_ruling`, verify, `RULED` CAS with reload and re-evaluate; race tests | A3 |
| The moot rule (§3.4) | B2 |
| Retire manual `rule` | A3b |
| Store refuses a new legacy-set `RULED` write | A4 |

### 7.2 Reader inventory

The new shapes are:
- a `purpose: 'dispute_ruling'` claim in `review_dispatch_claim`, which may be `COMPLETED` with `verdict: 'RULING'` or `'BLOCKED'`;
- a dispute entry in state `SUPERSEDED`;
- the new entry keys `arbiter_claim`, `history` and `superseded`;
- the ruling evidence keys.

A reader is classed **change** if it does any of the following:
- (a) selects a claim by version, by state, or by Reviewer Task or session, and could take a ruling claim as a formal review;
- (b) treats a dispute state other than `OPEN` as "no dispute" where blocking matters, or accepts states or keys from a closed set that would refuse or miscount the new ones;
- (c) reads a ruling as a withdrawal without a version scope.

Otherwise the reader is classed **no change**, with the reason given in the table.

**Readers in the files read at `8c4eac1f`:**

| Reader | Reads | Class | Reason | PR |
|---|---|---|---|---|
| `dispute_open_on` (`review_repair.py:72-80`) | disputes | change | `SUPERSEDED` must block (§3.5) | B1 |
| `withdrawn_findings` (`review_repair.py:83-93`) | disputes | change | Version scope (§4 item 6). It reads only `RULED`, so `SUPERSEDED` needs no change | A1d |
| `claim_reviewer` (`review_repair.py:125-140`) | claims | no change | A ruling claim's reviewer comes from its recorded `reviewer` (§2.2 step 12e). Formal claims are unchanged | none |
| `_failed_event` (`review_repair.py:325-367`) | claims | change | Takes `max(version)` at the head (`:336-344`) | A1b |
| `_review_gate` (`review_repair.py:508-581`) | disputes, through `withdrawn_findings` | change | Passes the packet's `version` (`:531`) | A1d |
| `repair_review` dispute check (`review_repair.py:689-691`) | disputes, through `dispute_open_on` | change | Distinct code for `SUPERSEDED` | B1 |
| `_auto_replay_claim` and repair counting (`review_repair.py:587-629`, `:712-733`, `:799-825`) | `review_repair_claims` | no change | Never reads `review_dispatch_claim` or `review_disputes` | none |
| `run_high_debug` dispute check (`high_debug.py:1537-1541`) | disputes, through `dispute_open_on` | change | Distinct code for `SUPERSEDED` | B1 |
| `_reserve_debug`, `_group_debug_used` (`high_debug.py:1361-1423`) | `high_debug` | no change | Never reads claims or disputes | none |
| every function in `lineage_counts.py` | `review_repair_claims`, `high_debug`, `continues`, completion result heads | no change | Never reads claims or disputes (§3.5) | none |
| `_dispute_task` (`task_submission_cli.py:2449-2460`) | disputes (shape only) | no change | Checks list shape only. Its abandoned refusal is reused (§3.5) | none |
| `_dispute_append` (`task_submission_cli.py:2746-2770`) | disputes | change | Must refuse `DISPUTE_SUPERSEDED` | B1 |
| `_dispute_fail_verdict` (`task_submission_cli.py:2720-2736`) | claims | change | Excludes ruling claims by text only (`:2726`) | A1b |
| `dispute_rule` (`task_submission_cli.py:2798-2873`) | claims, disputes | change | Disputed-claim lookup by `purpose` (`:2833-2838`, A1b); `DISPUTE_SUPERSEDED` (B1); retired (A3b) | A1b, B1, A3b |
| `_dispute_ruling_claim` (`task_submission_cli.py:2595-2646`) | claims | change | Uses the recorded `reviewer` and `arbiter_claim` (§2.2 step 12e) | A3 |
| `_dispute_review_claims` (`task_submission_cli.py:2709-2717`) | claims | no change | Returns every claim of a PR (and head). Its callers filter, and they are listed above | none |
| `_claude_review_claim_matches` (`task_submission_cli.py:3676-3696`), called by `run_task_complete` (`:8636-8656`) | claims | change | It accepts exactly one `COMPLETED` claim with the given Reviewer Task, PR, head and session. After A2b, a `COMPLETED` Claude ruling claim has that shape, so a completion naming the arbiter's Reviewer Task would pass this gate with a ruling. It must skip claims with `purpose`, so that such a completion fails closed with `REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT` (`:8648-8651`) | A1b |

**The five named status and event readers.** They are not in this Task's read paths, so they were not read, and this note does not classify them. The table records only what the files that were read show about how each is called.

| Reader | Known from the files read | Class |
|---|---|---|
| `tools/llmtool/llmtool/board_check.py` | Nothing | not classified: outside read scope |
| `tools/llmtool/llmtool/task_status.py` | `task status` and `task brief` call `task_status(pm_session, limit, store)` as a read-only projection (`task_submission_cli.py:1093-1119`) | not classified: outside read scope |
| `tools/llmtool/llmtool/chat_tui_status.py` | Nothing | not classified: outside read scope |
| `tools/llmtool/llmtool/chat_tui_events.py` | Nothing | not classified: outside read scope |
| `tools/llmtool/llmtool/github_events.py` | `task github-events` calls `poll(store, key, issues, since=..., github_get=...)` and reports comment events as `DISABLED_NO_TRUSTED_OWNER_SOURCE` (`task_submission_cli.py:8114-8121`) | not classified: outside read scope |

**PR R0 (docs)** reads these five files and fills in the class, reason and PR for each, using rules (a)-(c) above. Its only file is this note.
- A reader that shows a state should show `SUPERSEDED` as its own state, never as "no dispute".
- A reader that shows a review outcome must not show a ruling claim as a formal verdict.
- Any reader R0 classes as "change" gets its own PR (R1…Rn). If R0 finds no reader that needs a change, there are no R PRs.
- The §7.1 gate holds A2 and B1 until R0 and every R PR have merged.

### Proposed module: `tools/llmtool/llmtool/dispute_arbiter.py`

A new module holds every ruling-specific part, so formal review code keeps its behaviour. It has no GitHub I/O and posts nothing. Its only store write is the one step 11c CAS.

| Function | PR | Role |
|---|---|---|
| `render_ruling_prompt(*, rules_prefix, binding_line, finding_section, dispute_text, output_contract)` | A2 | Step 9. Returns the prompt; the dispute text is inside the untrusted fence. |
| `parse_ruling(text, *, actor, finding_id, pr, review_version, head, binding_line)` | A2 | §2.3. Returns `outcome` or refuses `DISPUTE_RULING_OUTPUT_INVALID`. |
| `parse_codex_ruling(raw, *, task_id, run_id, contract_digest, head, finding_id, pr, review_version)` | A2 | §2.3 Codex. Raises in the formal refusal shape (`review_dispatch_code = 'REVIEW_NOT_POSTED'` plus a `REVIEW_FAILURE_REASONS` reason), so `dispatch_arbiter`'s exception path records `UNKNOWN` as at `review_dispatch.py:1431-1491`. |
| `ruling_blocks(session_id, config_dir, reviewer_task)` | A2b | Step 11c C5. Returns `(output_sha256, assistant_texts)` or `None`. |
| `select_ruling(texts, *, binding_line, actor, finding_id, pr, review_version, head)` | A2b | Step 11c C6. Returns `(code, block, outcome)`. |
| `collect_claude_ruling(store, task, entry, claim, *, run=subprocess.run, claude=None, common_dir=None)` | A2b | Step 11c C1-C8. Returns a fixed code, or the `COMPLETED` claim after its one CAS. |

Imports: `review_transcript` (`session_state`, `_UUID`, `_REVIEWER`; read-only use, unchanged), `review_result._reviewer_session_bound`, `task_completion` (`read_d6_registration`, `_D6_EVIDENCE_STATES`, `_review_binding_line`) and `role_config`. `review_dispatch` imports this module, so this module imports `review_dispatch._unique_json_object` only inside `parse_codex_ruling`. That is the same lazy pattern `review_dispatch` uses for `review_result` (`review_dispatch.py:388`).

### PR A1: store schema and transitions (#2087, #2088)

Files:

- `tools/llmtool/llmtool/task_store.py`, all store changes that A1b-A3b and B1 need, so none of them touches this file:
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
  - **`SUPERSEDED`, for B1 and B2** (§3.3, §3.4). `_check_review_disputes_transition` accepts `OPEN` → `SUPERSEDED` only when all of these hold:
    - The write adds exactly `superseded` and one `SUPERSEDED` history event. Every other entry key is equal, and there is no `ruling`.
    - `superseded` has exactly the keys of §3.3, strictly typed:
      - `by_task` is 32 hex and is not this Task;
      - `by_run` and `by_contract_digest` are non-empty strings, and the digest differs from `old_contract_digest`;
      - `old_head` equals the entry's `head_sha`, and `new_head` is 40 hex and differs from it;
      - `evidence_sha256` equals the recomputed digest.
    - `arbiter` is `null` exactly when the entry has no `arbiter_claim`. Otherwise:
      - its `reviewer_task` and `version` equal `arbiter_claim`;
      - its `state` equals the state of that claim in the same record;
      - its `claim_sha256` equals the recomputed digest of that claim;
      - `moot_code` is `null` or one of `DISPUTE_STALE`, `DISPUTE_PR_NOT_OPEN`, `DISPUTE_VERDICT_NEWER`, `DISPUTE_NO_FAIL_VERDICT`. It must be non-null exactly when the claim is `COMPLETED` with `verdict: 'RULING'`.
    - The entry's arbiter claim is not `CLAIMED`.

    The store does not read the replacement record or the live head. B1 and B2 check those in the CLI.
  - **Terminal entries.** The store refuses any change to a `RULED` or `SUPERSEDED` entry. It also refuses any change to a `purpose: 'dispute_ruling'` claim whose entry is `RULED` or `SUPERSEDED`.
  - No change for abandoned records. A1 adds no write that is allowed on an abandoned Task (§3.5).
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
  - a `ruling.finding_id` or `ruling.review_version` that differs from the entry or the claim's `dispute`, a missing one, or a non-int or bool `review_version`;
  - a claim that is `BLOCKED`.
- **Refusals, entry:**
  - `history` shortened, rewritten or grown by two events;
  - `arbiter_claim` changed, or set without the matching `CLAIMED` claim in the same write.
- **Formal unchanged:**
  - a formal claim still round-trips `CLAIMED` → `COMPLETED` with each of `PASS`/`FIX`/`FAIL`/`BLOCKED`, and `CLAIMED` → `DISPATCHED`;
  - the #1636 settle of a formal claim is still accepted exactly as today.
- **Round trip, `SUPERSEDED`.** `OPEN` → `SUPERSEDED` is accepted and read back equal in five cases:
  - an entry with no `arbiter_claim`;
  - an arbiter claim that is `DISPATCHED`;
  - an arbiter claim that is `UNKNOWN`;
  - an arbiter claim that is `COMPLETED` `BLOCKED`, with `moot_code: null`;
  - an arbiter claim that is `COMPLETED` `RULING`, with each of the four moot codes.
- **Refusals, `SUPERSEDED`:**
  - an arbiter claim that is `CLAIMED`;
  - a `COMPLETED` `RULING` claim with `moot_code: null`, or a `moot_code` on any other claim;
  - an `arbiter` snapshot whose state, version, Reviewer Task or `claim_sha256` differs from the claim;
  - an `arbiter` value for an entry without `arbiter_claim`, or `null` for an entry with one;
  - a wrong `evidence_sha256`;
  - a missing or extra `superseded` key;
  - `new_head == old_head`, or an `old_head` unequal to the entry's `head_sha`;
  - a `ruling` key;
  - no history event, or two;
  - any other entry key changed.
- **Terminal.** Every change to a `RULED` or `SUPERSEDED` entry is refused: state back to `OPEN`, `superseded` or `ruling` edited, a history event removed or added, `arbiter_claim` changed. A `DISPATCHED` → `COMPLETED` write on a ruling claim whose entry is `SUPERSEDED` is also refused. After each refusal the stored record is byte-equal to before.

### PR A1b: formal readers skip ruling claims (#2087)

Files:

- `tools/llmtool/llmtool/review_repair.py`: `_failed_event` (`:336-344`) leaves out `purpose: 'dispute_ruling'` claims before taking `max(version)`.
- `tools/llmtool/llmtool/review_dispatch.py`: the prior-claim branch (`:1231-1262`) refuses `REVIEW_DISPATCH_VERSION_USED` for a `dispute_ruling` claim and posts nothing.
- `tools/llmtool/llmtool/task_submission_cli.py`:
  - `_dispute_fail_verdict` (`:2724-2729`) and the disputed-claim lookup (`:2833-2836`) also exclude by `purpose`;
  - `_claude_review_claim_matches` (`:3676-3696`) leaves out claims with `purpose` before it counts matches (§7.2).
- `tools/llmtool/tests/test_dispute_ruling_claim_readers.py`.

Tests (claims are built in the test as plain records; no runtime is started):

- With a FAIL claim at version N and a `dispute_ruling` claim at N+1 on the same head, `_failed_event` still returns the version-N verdict packet (injected `github_get` lists the version-N comment only). This holds for a `COMPLETED` ruling claim, a withheld one (`BLOCKED`, empty text) and an `UNKNOWN` one.
- `dispatch_review` with the version of a `dispute_ruling` claim refuses `REVIEW_DISPATCH_VERSION_USED`, and the injected `comment_poster` is never called.
- A `dispute_ruling` claim, with or without ruling text, is never picked as the disputed FAIL verdict.
- `_claude_review_claim_matches` with the Reviewer Task, PR, head and session of a `COMPLETED` ruling claim returns `False`. The same arguments for a `COMPLETED` formal claim still return `True`, and with two matching formal claims it still returns `False`, as today.
- Without any `dispute_ruling` claim, every reader returns exactly what it returns today (no check weakened).
- **Counts equal.** With an arbiter claim present in each state, `lineage_counts`, `chain_review_repairs` and `chain_debug_used` return exactly the values they return without it (§3.5).
- **Merge-gate readers** (`task_advance.py`, read at `c1ab1339`; tested here, not changed). With a formal PASS at version N, its `REVIEWER` event and a `dispute_ruling` claim at N+1 in each state, `_merge_pass_claim` (`:1167-1214`) never returns N+1 as a PASS, and returns `MERGE_NEWER_REVIEW_EXISTS` as today's newer-claim veto gives (`:1198-1206`). With a formal PASS at N+2, it returns N+2. `_review_outcomes` (`:2598-2659`) returns no outcome for the ruling claim (injected `github_get` lists a byte-equal ruling comment).
- The PR description lists every reader of `review_dispatch_claim` and `review_disputes` found by grep, including merge-gate code. Each one is either:
  - classified in §7.2,
  - changed and tested here or in A1c, or
  - shown not to select by version, state or Reviewer Task.

  If a reader needs a fifth file, it gets its own PR before A2 and B1, in the same way as the R PRs. The readers in `review_result.py` and `review_transcript.py` are already known and are covered by A1c.

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

### PR A1d: version-scoped withdrawal (#2087)

Files:

- `tools/llmtool/llmtool/review_repair.py`:
  - `withdrawn_findings(task, pr, head, version)` (`:83-93`) is version-scoped as §4 item 6 states.
  - `_review_gate` passes `packet.get('version')` at its one call (`:531`).
  - `dispute_open_on` is not changed.
- `tools/llmtool/tests/test_dispute_withdrawal_version.py`.

Tests (plain records; `_review_gate` runs for real with fixed policy and counts):

- A `RULED` `WITHDRAWN` entry for F1 at version N (legacy key set and `arbitrate` key set) withdraws F1 for `version == N` only. For `N+1` at the same head and PR it withdraws nothing.
- `_review_gate` with a version-N packet whose only blocking finding is F1 refuses `REPAIR_NO_BLOCKING_FINDING`, as today. With a version-N+1 packet that reuses the ID F1 for a blocking finding, it does not refuse on that ground and goes on to the repair limit checks.
- A packet without `version`, or with a bool or non-int one, withdraws nothing.
- An `arbitrate` ruling whose `ruling.review_version` differs from the entry's `review_version` withdraws nothing. (The A1 store already refuses such an entry; this is defence in depth.)
- `UPHELD`, `OPEN`, `DEFERRED` and `SUPERSEDED` entries withdraw nothing at any version, as today.

### PR A2: `arbitrate` launch and Codex completion (#2087)

Files:

- `tools/llmtool/llmtool/dispute_arbiter.py` (new): `render_ruling_prompt`, `parse_ruling`, `parse_codex_ruling`.
- `tools/llmtool/llmtool/review_dispatch.py`: `dispatch_arbiter`, reusing `_preflight`, `_create_d6`, `_dispatch`, `_dispatch_codex` and `_bind_d6`. `_dispatch_codex` gains `parser=_parse_codex_review` (`review_dispatch.py:644-683`); every formal call is unchanged.
- `tools/llmtool/llmtool/task_submission_cli.py`: `arbitrate` parser and adapter, steps 1-11a and 11b. Step 2 refuses `DISPUTE_SUPERSEDED` for a `SUPERSEDED` entry, so B1 is already in place. The post-launch write is one of step 11a, step 11b, or the `UNKNOWN` exception path. It re-reads the record and makes its CAS on that fresh read. If the read shows the Task abandoned, it writes nothing and reports `DISPUTE_TASK_ABANDONED` (§3.5).
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
  - a ruling with the wrong actor, finding, PR, review version, head or binding line, or a legacy header without `Version:`;
  - a text that also holds a `review_verdict` marker or a `Verdict:` line.
- **Newer verdict before launch.** With a newer formal claim at the disputed head (one test each for `COMPLETED`, a Claude `DISPATCHED` claim with a matching `REVIEWER` event, a `CLAIMED` claim, and a `REVIEWER` event with no claim), step 3 refuses `DISPUTE_VERDICT_NEWER`. No claim, D6 or launch follows, and the record is byte-equal to before.
- **Formal Codex unchanged.** A formal `dispatch_review` call on the same patched runtime with a formal JSONL fixture returns exactly today's result. `_dispatch_codex` without `parser` still calls `_parse_codex_review`.
- **Claude launch.** An injected `dispatch_session` and `bind_session` give `CLAIMED` → `DISPATCHED` with the returned session. The call reports `ARBITER_PENDING`, and no transcript or comment is read.
- **No GitHub output source.** In steps 7-11, the only GitHub read is the one step 6 read of the PM's dispute-text comment (injected `github_get` called exactly once with that comment's path). `comment_lister` and `comment_poster` are never called.
- A screen refusal (monkeypatched `_screen_text`) on the Codex ruling records `BLOCKED` with empty text, and the dispute stays `OPEN`.
- The dispute text comment (injected `github_get`) that is edited, created after `opened_at`, or refused by a monkeypatched `_screen_text` refuses before any write, D6 or launch. A valid one has its digest on the stored claim, and the rendered prompt carries that exact text inside the untrusted fence.
- **Supersede vs launch and Codex completion** (real `TaskStore`; B1 merged first):
  - **During the run.** The monkeypatched Codex `subprocess.run` calls `supersede` before it returns. `supersede` refuses `DISPUTE_ARBITER_IN_FLIGHT` and writes nothing. Then step 11a writes `COMPLETED` once. A `supersede` after that refuses `DISPUTE_RULING_PENDING` (B1).
  - **Supersede first.** The entry is `SUPERSEDED` before `arbitrate` runs. `arbitrate` refuses `DISPUTE_SUPERSEDED` at step 2: no claim, no D6, no launch, and the record is byte-equal.
  - **Supersede between the read and step 7.** The injected `current_head` reader of step 2 writes `SUPERSEDED` through the real store. Step 7's CAS loses, and there is no D6 and no launch. A replay refuses `DISPUTE_SUPERSEDED`.
- **Abandoned during the run.** The monkeypatched `subprocess.run` (Codex) or the injected `dispatch_session` (Claude) abandons the old Task through the real store before it returns. Step 11a, step 11b or the `UNKNOWN` path then writes nothing, and the claim stays `CLAIMED`. The call reports `DISPUTE_TASK_ABANDONED`, and a replay refuses `DISPUTE_TASK_ABANDONED` with no write.

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
  - a bound ruling with the wrong actor, finding, PR, review version or head;
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
- **Collection is a claim-truth write (§2.4).** With a newer formal claim already at the head, a bound ended-session ruling still moves the claim `DISPATCHED` → `COMPLETED`. Step 12 does not exist until A3, so nothing is posted or ruled here. A3's race tests cover the step 12 refusal that follows.
- **Supersede vs collection** (real `TaskStore`; B1 merged first):
  - **Supersede wins.** The injected agents-listing `run` calls `supersede` (which goes on, because the claim is `DISPATCHED`) and then returns `ENDED`. C8's CAS loses and writes nothing. The claim is still `DISPATCHED`, and the entry is `SUPERSEDED` with `arbiter.state == 'DISPATCHED'`. A replay refuses `DISPUTE_SUPERSEDED` at step 2: `run` is not called again, and the transcript is not opened.
  - **Collection wins.** C8 writes `COMPLETED` `RULING`. A `supersede` after that refuses `DISPUTE_RULING_PENDING` (B1). With a withheld result (`BLOCKED`), the `supersede` goes on.
  - **Abandon between step 2 and C8.** The injected `run` abandons the old Task through the real store and returns `ENDED`. C8's CAS loses and writes nothing, and a replay refuses `DISPUTE_TASK_ABANDONED`.

### PR A3: step 12, freshness and race tests (#2087)

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`:
  - `_dispute_rule_verified`;
  - step 12: 12a identity, F1/F2/F3, and the `RULED` CAS with reload and re-evaluate;
  - F, written as two functions. `_ruling_current` reloads (§2.2 step 12b check 1) and refuses an abandoned record with `DISPUTE_TASK_ABANDONED` (§3.5). It then calls `_ruling_check(record, *, identity, arbiter_claim, claim_sha256, current_head)`, which runs checks 2-5 (live head, dispute state, arbiter claim, check N) on the record it is given and writes nothing. F behaves as §2.2 step 12b states. B2 calls `_ruling_check` on its own round's read;
  - `_dispute_ruling_comment` gains the optional `review_version`;
  - `ruling` evidence keys and digests.
- `tools/llmtool/llmtool/review_dispatch.py`: `_find_posted_ruling`; `_post_under_lock` picks the matcher from the claim's recorded `purpose` at both match sites (`:1120-1122`, `:1163`); `_receipt_identity` adds `dispute` for ruling claims; the post result returns the comment id. `_find_posted_verdict` and formal receipts are not changed.
- `tools/llmtool/tests/test_dispute_arbitrate_rule.py`.
- `tools/llmtool/tests/test_dispute_arbitrate_race.py`.

Common setup for both test files: the real `TaskStore` on a temporary directory; injected `current_head`, `github_get`, `comment_lister`, `comment_poster` and runtime; fixed placeholder ids, heads (`'a' * 40` style) and digests. Every "no write" result checks that the stored record is byte-equal to before.

Tests, `test_dispute_arbitrate_rule.py`:

- A full arbitrate run with injected runtime and GitHub writes `RULED` with the evidence keys of §2.6 item 2 (including `finding_id` and `review_version`), bound to its `COMPLETED` claim through the real `TaskStore`. It runs for both a Claude arbiter (`account_mode` `ROLES` or `EXPLICIT`, collected by A2b) and a Codex arbiter (`ROLES` and `CODEX_CLI`). This pins the §1.3 item 5 gap as closed.
- The posted body is the stored claim `review_text`, byte for byte, and its header carries `Version: <D.review_version>`. No step-12 path completes or changes the claim.
- A ruling claim whose recorded `reviewer` differs from the chosen arbiter, or whose marker actor differs from the recorded `reviewer`, refuses `DISPUTE_RULING_CLAIM_INVALID`. A formal claim still goes through `claim_reviewer` unchanged.
- An arbiter claim missing `executor_task`, `pr` or `head_sha`, or whose `dispute` differs from the entry in any one of the four values of `D`, refuses.
- **Receipt identity.** The ruling receipt records `dispute` equal to `D`. A stored receipt for the same name whose `dispute.review_version` or `dispute.finding_id` differs refuses `VERDICT_POST_RECEIPT_UNAVAILABLE`, reports `ARBITER_POST_PENDING`, and the poster is never called. A formal claim's receipt identity is byte-equal to today's.
- A post result of `IN_PROGRESS`, `POSTED_UNVERIFIED` or one without a comment id reports `ARBITER_POST_PENDING` and writes nothing. The replay then rules without a second post (injected `comment_poster` called once).
- The `evidence_ref` passed to verification is built from the trusted slug and the receipt comment id. The PM-supplied value is never consulted.
- The generated `reviewer_task` and the injected runtime session pass the existing `D6_TASK_ID` / `valid_review_dispute_session` checks. The test pins the shape and keeps the check.
- **Disputed FAIL present (ruling-aware matching).** The injected `comment_lister` already returns, by the same login and with the same binding line:
  - the disputed FAIL comment at `D.review_version`;
  - a `review_verdict` comment of another version;
  - a FAIL comment whose free text holds a `Ruling: ` line;
  - a ruling on another finding at the same head;
  - a ruling on the same finding at the same head for another review version (versioned header).

  The ruling is posted once (injected `comment_poster` called once), the receipt records its comment id, and `RULED` is written. A replay with the posted ruling now in the list returns `ALREADY_POSTED` with the same id and posts nothing. The same list run directly through `_find_posted_ruling` returns (no match, no conflict, no duplicate) before the post and (the ruling, no conflict, no duplicate) after.
- **Conflict within the ruling identity.** Each of these, by the same login, refuses `DISPUTE_RULING_POST_CONFLICT`; the poster is never called and nothing is written:
  - a different versioned ruling for the same finding, PR, head and review version;
  - a legacy-header ruling for the same finding, PR and head;
  - an unparsable ruling-shaped comment.
- **Duplicate within the ruling identity.** Two exact copies of the ruling in the list refuse `DISPUTE_RULING_POST_CONFLICT`, and nothing is written.
- **Formal matcher unchanged.** A formal claim posted with the same list still refuses `VERDICT_POST_CONFLICT`, exactly as today. Passing a ruling-shaped list for a formal claim does not switch the matcher.
- An edited comment, or one posted before `opened_at`, refuses.
- A Developer session, the first-review session, or a D6 other than the claimed `reviewer_task` refuses.
- `formal_pass_required` stays true. `withdrawn_findings(task, pr, head, D.review_version)` changes only after `RULED WITHDRAWN`, and `withdrawn_findings(task, pr, head, D.review_version + 1)` never does.

Tests, `test_dispute_arbitrate_race.py`. Each "newer verdict" case runs in three forms, one per check-N case that a real review produces: a newer formal `COMPLETED` claim; a newer formal Claude claim that is `DISPATCHED` with a matching `REVIEWER` completion event (the event-backed outcome); and a `REVIEWER` event at a newer version with no claim. Each newer verdict is written through the real `TaskStore`, never by editing a cached record. Each form runs once with a Codex arbiter and once with a Claude arbiter.

- **Newer verdict before replay.**
  - With a `COMPLETED` `RULING` arbiter claim stored, the newer verdict is written, then `arbitrate` replays. F1 refuses `DISPUTE_VERDICT_NEWER`. The poster and lister are never called, there is no receipt, no `RULED`, the entry is `OPEN`, and the record is byte-equal to before the replay.
  - The same with a `DISPATCHED` Claude arbiter claim and a bound ended-session ruling: the replay writes the claim-truth `COMPLETED` (A2b), then F1 refuses. The poster is never called, and there is no `RULED`.
  - A pending newer formal claim (`CLAIMED`, `DISPATCHED` without an event, `UNKNOWN`) also refuses at F1.
- **Newer verdict during posting.**
  - **Before the fence.** The injected `comment_lister` writes the newer verdict, then returns the list. F2 (the `allowed` callback) refuses. The poster is never called, the receipt is `FAILED` with `VERDICT_POST_NOT_ALLOWED`, and the call reports `DISPUTE_VERDICT_NEWER`, not the post code. There is no `RULED`.
  - **During the post.** The injected `comment_poster` writes the newer verdict, then returns the created comment. The poster is called once and the receipt is `POSTED` with the comment id. F3 refuses `DISPUTE_VERDICT_NEWER`, there is no `RULED`, and the entry stays `OPEN`. A later replay refuses at F1 and never calls the poster again.
- **Newer verdict between the final read and the CAS.** The injected `current_head` reader returns the disputed head. On its F3 call, which runs after F3's `store.get`, it writes the newer verdict. F3's checks pass on the record it already read. The `RULED` compare-and-update then loses on `record_sha256`, the next round reloads, and check N refuses `DISPUTE_VERDICT_NEWER`. There is no `RULED`, and the record holds the newer verdict and an `OPEN` entry.
- **Newer verdict after `RULED`.**
  - After `RULED` `WITHDRAWN` for F1 at version N, a newer formal FAIL at N+2 that reuses ID F1 is written. A replay returns the recorded `RULED` result with no post and no write.
  - `withdrawn_findings(task, pr, head, N + 2)` is empty. `_review_gate` on the N+2 packet does not refuse `REPAIR_NO_BLOCKING_FINDING` because of the ruling.
  - The `RULED` entry is byte-equal to before.
- **CAS loser re-evaluates.**
  - An unrelated write (another Task field) injected between F3's read and the CAS makes the first CAS lose. The second round passes and writes `RULED` once.
  - Two `arbitrate` calls race on the same `COMPLETED` claim: the poster is called once, exactly one `RULED` is written, and the loser returns the winner's recorded result with no write.
  - Three injected conflicts in a row report `DISPUTE_RULE_CAS_CONFLICT` with no write. The next replay rules without a second post.
- **Fresh reads.** A counting wrapper around the injected `current_head` and around `TaskStore.get` (it calls the real method) shows one reload and one live head read at each of F1, F2 and every F3 round, and shows each head read comes after its reload.
- The event-only form writes the `REVIEWER` event through whichever completion-event write path the real store accepts. Which path that is was not re-read here (UNKNOWN); the PR names it. It never bypasses a store check.
- **Head move.** A head move found at F1, F2 or F3 refuses `DISPUTE_STALE` with no `RULED`. A move at F3 happens after the post; the comment stays and rules nothing.
- **Dispute state changes during posting.** The injected `comment_poster` makes the entry `SUPERSEDED` (A1 schema, real store, with a moot code, as the A1 store requires for a `COMPLETED` `RULING` claim). F3 refuses `DISPUTE_SUPERSEDED`, and there is no `RULED`. The entry, its `superseded` value and the `COMPLETED` arbiter claim are byte-equal to what the supersede wrote, and the posted comment rules nothing. A replay refuses `DISPUTE_SUPERSEDED` at step 2 and never calls the poster.
- **Supersede after `RULED`.** After `RULED`, `supersede` refuses `DISPUTE_ALREADY_RULED`, and the record is byte-equal.
- **Abandoned during posting.** The injected `comment_poster` abandons the Task through the real store. F3 refuses `DISPUTE_TASK_ABANDONED`, there is no `RULED`, and the entry stays `OPEN`.
- **`_ruling_check` writes nothing.** On each F refusal case above, `_ruling_check` called directly on the same record raises the same code, and the store's record is byte-equal.

### PR A3b: retire manual `rule` (#2087)

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`: `rule` refuses `DISPUTE_RULE_MANUAL_RETIRED` before reading any argument value; its options are removed (§2.5). `_dispute_ruling_comment` then always requires the versioned header.
- `tools/llmtool/tests/test_review_dispute.py` (existing dispute tests, if present, updated for retirement only, with no check weakened). If that file has another name, the PR names it; UNKNOWN here.
- `tools/llmtool/tests/test_dispute_supersede.py`: only the B1 tests that call `rule` change, to expect `DISPUTE_RULE_MANUAL_RETIRED`. The tests for `open` and `defer` stay as they are.

Tests:

- `rule` with any arguments refuses `DISPUTE_RULE_MANUAL_RETIRED` and writes nothing, including on a `SUPERSEDED` entry.
- Every A3 and B1 test still passes, with no change other than the one above (the PM runs all three files).

### PR A4: store refuses new legacy rulings (#2087)

Files:

- `tools/llmtool/llmtool/task_store.py`: `_check_review_disputes_transition` (`:1538-1560`) accepts a new `RULED` write only with the `arbitrate` key set (§2.6 item 2).
- `tools/llmtool/tests/test_dispute_store_legacy_ruling.py`.

Tests (real `TaskStore`):

- A new `OPEN` → `RULED` write with today's key set is refused `REVIEW_DISPUTE_TRANSITION_INVALID`, and the record is unchanged.
- A stored entry already `RULED` with today's key set still validates and reads back.
- The `arbitrate` write of A1's round trip is still accepted.

### PR R0: classify the five status and event readers (docs)

Files: this note only. R0 reads `board_check.py`, `task_status.py`, `chat_tui_status.py`, `chat_tui_events.py` and `github_events.py`, and fills in the second table of §7.2 with a class, a reason with file:line, and a PR. Tests: none (docs only). Each R1…Rn PR that follows changes one reader. Its tests show three things:
- a ruling claim in each state is never shown or used as a formal verdict;
- a `SUPERSEDED` entry is shown as `SUPERSEDED`, never as "no dispute";
- without the new shapes, the reader's output is byte-equal to today's.

### PR B1: `supersede` (#2088)

B1 merges after A1 and the R gate (§7.1), and before A2. Arbiter claims are built in its tests as A1-schema records written through the real store; no runtime is started.

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`:
  - the `supersede` subparser (§3.1) and adapter, with `pm_caller.check_caller` on both Tasks;
  - §3.2 items 1-7 with the B1 pending rule, the §3.3 write and the bounded rounds;
  - `_dispute_append` and `dispute_rule` refuse `DISPUTE_SUPERSEDED` for a `SUPERSEDED` entry at the same (pr, finding, head);
  - `_DISPUTE_ACTIONS` gains `supersede`.
- `tools/llmtool/llmtool/review_repair.py`: `dispute_block_on`; `dispute_open_on` built on it; `repair_review` refuses `REPAIR_DISPUTE_SUPERSEDED` (§3.5).
- `tools/llmtool/llmtool/high_debug.py`: `run_high_debug` refuses `DEBUG_DISPUTE_SUPERSEDED` at the existing check (`:1537-1541`).
- `tools/llmtool/tests/test_dispute_supersede.py`.

B1 does not touch `task_store.py`: the `SUPERSEDED` schema lands in A1, and no write on an abandoned record is added (§3.5).

Tests. They use the real `TaskStore` on a temporary directory. `pm_caller.check_caller` runs on PM sessions the test records, or is injected for the refusal cases. The test injects `task_completion.read_d6_registration` and `collect_git_snapshot` with fixed placeholder records. Ids, heads and digests are fixed placeholder shapes, such as 32-hex Task ids with one varied digit, `'a' * 40` and `'b' * 40` heads, and `'0' * 64` digests. No credential-shaped strings are used. Every "no write" result checks that both stored records are byte-equal to before.

- **Success; unchanged replacement record.**
  - A valid direct successor with a clean new head writes `SUPERSEDED`, the `superseded` value of §3.3 with the right `evidence_sha256`, and one history event.
  - In the old record only `review_disputes` changed, and within it only this entry.
  - The replacement record, including its `record_sha256`, is byte-equal to before. It has no `review_disputes` key added.
- **Replay.** An identical call returns the recorded result with no write. A call whose replacement D6 head has moved refuses `DISPUTE_ALREADY_SUPERSEDED` with no write.
- **Fail-closed cases.** Each case of §3.7 refuses with its own code and writes nothing. This includes three `OPEN` entries for one (pr, finding) on three heads: `--head` selects exactly one, and the other two stay `OPEN`.
- **Arbiter claim states.**
  - `CLAIMED` refuses `DISPUTE_ARBITER_IN_FLIGHT`.
  - `COMPLETED` `RULING` refuses `DISPUTE_RULING_PENDING`.
  - `DISPATCHED`, `UNKNOWN` and `COMPLETED` `BLOCKED` go on, with the `arbiter` snapshot equal to the claim. The claim is byte-equal before and after.
- **Immutable history.** After `SUPERSEDED`:
  - `open`, `defer` and `rule` on the same (pr, finding, head) refuse `DISPUTE_SUPERSEDED` with no write;
  - a direct `compare_and_update` on the real store is refused if it sets the entry back to `OPEN`, edits `superseded`, adds or removes a history event, adds `ruling`, or changes the arbiter claim;
  - `task abandon` of the old Task leaves `review_disputes` byte-equal;
  - the `history` list is the earlier events plus exactly one `SUPERSEDED` event.
- **Preserved old-head refusals.** On the old Task at the old head after `SUPERSEDED`:
  - `repair_review` refuses `REPAIR_DISPUTE_SUPERSEDED` with no repair claim written, using an injected `github_get`, `current_head` and `dispatch_adapter`, where the adapter is never called;
  - `run_high_debug` refuses `DEBUG_DISPUTE_SUPERSEDED` with no `high_debug` entry and no D6 create;
  - `dispute_open_on` is `True` and `withdrawn_findings` is empty;
  - a test-failure repair is not blocked, as today;
  - with an `OPEN` and a `SUPERSEDED` entry at one head, and with a malformed list, the code is `REPAIR_DISPUTE_OPEN` / `DEBUG_DISPUTE_OPEN`, as today;
  - without any `SUPERSEDED` entry, every caller returns exactly today's result.
- **Replacement not blocked.** On the replacement Task at the new head, `dispute_open_on` is `False`. Its `repair_review` gets past the dispute check and goes on to its next existing check.
- **Inherited counts.** The old Task holds `k` review repair claims (`k` from 1 to 3) and `m` used High Debug entries. The replacement holds none. The same values hold before the supersede, after it, and after the old Task is abandoned:
  - `lineage_counts(store, replacement, base_family=..., include_successors=True)` gives `review_repairs == k` and the old heads in `review_heads`;
  - `chain_review_repairs(store, replacement) == k`;
  - `chain_debug_used` on the replacement is `m`;
  - the old Task's own counts are equal.
- **UNKNOWN-count refusal.** Each of these refuses `DISPUTE_CHAIN_UNKNOWN`, and both records are byte-equal:
  - a fork, made by a second record written straight to the store that also continues the old Task;
  - a malformed `review_repair_claims` value on the old Task;
  - an unreadable predecessor of the old Task;
  - a chain key that changes between the first read and the locked read.

  On the same forked chain, `repair_review` on the replacement still treats its count as the limit, as today. A count is never 0 by doubt.
- **Abandoned records.**
  - An already abandoned old Task refuses `DISPUTE_TASK_ABANDONED`, and the entry stays `OPEN`.
  - An abandoned replacement refuses `DISPUTE_REPLACEMENT_INVALID`.
- **Races with completion and finalization.** The concurrent write is made through the real store from inside the injected `read_d6_registration`, which runs after the round's read and before its CAS. Each case checks the code, the stored record, and that exactly one terminal state (or none) results:
  - the `CLAIMED` arbiter claim appended (step 7 shape): the next round refuses `DISPUTE_ARBITER_IN_FLIGHT`;
  - the claim moved `DISPATCHED` → `COMPLETED` `RULING` (C8 shape): `DISPUTE_RULING_PENDING`;
  - the claim moved `DISPATCHED` → `COMPLETED` `BLOCKED`: the next round goes on and writes `SUPERSEDED` once;
  - `RULED` written with the `arbitrate` set: `DISPUTE_ALREADY_RULED`;
  - the old Task abandoned: `DISPUTE_TASK_ABANDONED`;
  - an unrelated field written: the next round writes `SUPERSEDED` once;
  - three conflicts in a row: `DISPUTE_SUPERSEDE_CAS_CONFLICT` with no write;
  - two `supersede` calls in two threads on the same entry: exactly one write, and the other call returns the recorded result.
- **Supersede first, then completion.** With the entry `SUPERSEDED` over a `DISPATCHED` arbiter claim, the C8-shaped write `DISPATCHED` → `COMPLETED` through the real store is refused, and the claim stays `DISPATCHED`.
- **CAS exception type.** The test pins the exception type the real store raises for a changed `record_sha256` (§3.3).

### PR B2: the moot rule (#2088)

B2 merges after A3 and B1.

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`: for a `COMPLETED` `RULING` arbiter claim, `supersede` calls `_ruling_check` on its round's read and applies §3.4. A pass refuses `DISPUTE_RULING_PENDING`. A moot-set code writes `SUPERSEDED` with `moot_code`. Any other code refuses with that code.
- `tools/llmtool/tests/test_dispute_supersede_moot.py`.

Tests. They use the real `TaskStore`, an injected `current_head`, and injected `github_get`, `comment_lister` and `comment_poster`.
- `comment_lister` and `comment_poster` are never called in any B2 test.
- `github_get` is called only through the live head read.

The cases:
- **F passes:** `DISPUTE_RULING_PENDING`, no write. A following `arbitrate` rules as in A3, and a `supersede` after it refuses `DISPUTE_ALREADY_RULED`.
- **Moot:** one test for each of the four codes. The moot condition is a head moved off `D.head_sha`, a closed or merged PR, a newer formal outcome written through the real store, or the disputed claim gone. The call writes `SUPERSEDED` with that `moot_code`, and the `COMPLETED` claim is byte-equal. A following `arbitrate` refuses `DISPUTE_SUPERSEDED` at step 2 and never calls the poster.
- **Not moot:** an unreadable head (`DISPUTE_HEAD_UNKNOWN`) or a claim digest mismatch refuses with that code and writes nothing.
- **Order.** A counting wrapper shows that the live head read comes after the round's `store.get`.
- **B1 unchanged for other states:** every B1 arbiter-state test gives the same result.

### PR D1: rule-file text (docs)

Files: `CLAUDE.md`, `AGENTS.md`, `skills/ai-team-delivery/pm-continuation.md`. These describe `arbitrate`, the retired `rule`, and `supersede` with its PM order (supersede, then abandon; §3.5), after A3b and B2 merge. Tests: none (docs only). The existing doc-consistency checks, if any, run in CI.

---

## 8. Open points for review

### 8.1 Resolved

Resolved by the O7 re-read at `e7418cf3` (child 1/3) and `c1ab1339` (child 2/3). These are no longer open:

- A Claude-route ruling cannot complete through `review_result.py`. That module reads stored results only for `COMPLETED` claims, and otherwise reads GitHub comments (`review_result.py:359-373`), which this design never uses for runtime output. Collection lives in `dispute_arbiter.py` (PR A2b). `review_result.py` only refuses ruling claims (PR A1c).
- The readers of `review_dispatch_claim` in `review_result.py` and `review_transcript.py` are `_claim_for` (exact version, `:161-176`), `_completed_claim_result` (`:268-296`), `_settle_candidate` (`:182-214`) and `unposted_verdict` (`review_transcript.py:90-117`). PR A1c covers them.
- The `task_store.py` validators and their lines:
  - claim keys `:2028-2081`;
  - `review_result` `:2082-2094`;
  - claim transitions `:5490-5540`;
  - `ruling` dict `:1477-1488`;
  - dispute entries and transitions `:1491-1560`.

  PR A1 changes them as §7 states.
- (O7 child 2/3, `c1ab1339`.) The merge-gate readers in `task_advance.py` are `_merge_pass_claim` (`:1167-1214`) and `_review_outcomes` (`:2598-2659`). Neither takes an outcome from a claim without a `REVIEWER` event at its version. A1b pins them (§4 item 10).
- `'reviewer-' + 20 hex` matches `D6_TASK_ID` (`task_store.py:58`). The session shape fits `valid_review_dispute_session` (`:1470-1474`). Only its `_emitted_sensitive` part is open (below).

Resolved by this child (O7 child 3/3, read at `8c4eac1f`):

- **Lineage counting and the arbiter claim.** `lineage_counts.py` never reads `review_dispatch_claim` or `review_disputes` (§3.5). So a `dispute_ruling` claim is never counted as a review round, a repair, a failed head or a Debug, and supersede changes no count. This answers the last bullet of §4 item 10. A1b and B1 pin the counts as equal, not just "never lower".
- **Abandoned old Task.** No dispute flow writes an abandoned record (§3.5). Supersede refuses `DISPUTE_TASK_ABANDONED`, as `_dispute_task` already does (`task_submission_cli.py:2455-2456`), and the `OPEN` entry blocks the old head exactly as `SUPERSEDED` would. No store exception is needed, so the question about the `task_store.py` write path is no longer needed. This replaces the §6 `supersede` bullet "abandoned old Task not writable (if B1 finds that)" (see §3.7).
- **A `COMPLETED` ruling that check N refuses.** It is moot, not pending (§3.4, PR B2). B1 alone keeps the strict rule, so if the Owner declines the moot rule through the PM, B2 is simply not merged and nothing else changes. This closes the open point at the end of §2.4.
- **Supersede races** with step 7, 11a, 11b, 11c C8 and 12e, with abandon, and with a second supersede: the §3.4 table. This details the §5 row "Race supersede vs arbitrate" and adds `DISPUTE_ARBITER_IN_FLIGHT`.
- **Chain proof.** Supersede proves the chain with `chain_key` under the lock and with known `lineage_counts`. Any doubt refuses `DISPUTE_CHAIN_UNKNOWN` (§3.2 items 2 and 5). This extends the §5 row "Forged replacement".
- **A further in-scope reader.** `_claude_review_claim_matches` (`task_submission_cli.py:3676-3696`), used by `run_task_complete` (`:8636-8656`), would accept a `COMPLETED` Claude ruling claim. It is changed in A1b (§7.2).
- **Merge order.** The old line "B1 needs only A1" is replaced by §7.1. B1 also needs the R gate, and B2 needs A3.
- **Citation update.** The `continues` lines cited in §1.5 have moved: `record-pm --continues` is now at `task_submission_cli.py:8049-8062` and the write at `:8091-8092`. `_contract` (`:535-542`) is unchanged.

### 8.2 Not settled by this child: five readers (BLOCKED here)

`board_check.py`, `task_status.py`, `chat_tui_status.py`, `chat_tui_events.py` and `github_events.py` are not in this Task's read paths. They were not read, and §7.2 does not classify them. This is the one point of the rollout that this note does not settle. The §7.1 gate keeps it from being skipped:
- PR R0 classifies the five readers;
- any change gets its own R PR;
- A2 (the first arbiter claim) and B1 (the first `SUPERSEDED` entry) do not merge until R0 and every R PR have merged.

The same gate covers any further reader that A1b's grep finds, including merge-gate code other than `_merge_pass_claim` and `_review_outcomes` in `task_advance.py`, and any reader of `ruling.outcome` outside `review_repair.py`. In `llmtool/`, the only caller of `withdrawn_findings` found is `_review_gate` (`review_repair.py:531`). A1d's PR description lists the grep, and any reader found is version-scoped in its own PR before A3.

### 8.3 Pinned by test; fail-closed either way

None of these points is foundational. Each has a stated outcome if the answer is "no", and that outcome refuses rather than allows:

- Whether `_emitted_sensitive` (`test_output_excerpts.py`) passes the generated Reviewer Task and runtime session ids. A3 pins it with fixed placeholder ids. A refusal makes the ruling fail closed, and any change is its own reviewed PR that never removes the check.
- Whether a Reviewer D6 made by `_create_d6` records `result_sha` equal to its `--base-sha` (`d6_worktree_lifecycle.py`, out of scope). A2 pins it. If not, the ruling is refused `DISPUTE_ARBITER_UNVERIFIED`, and any lifecycle change is a separate PR.
- `role_config.resolve_role('reviewer')` field names (`account`, `config_dir`, `source`, `provider`). They are taken from their use at `review_transcript.py:97-100` and `review_dispatch.py:1284-1315`; `role_config.py` was not read. A2b uses them exactly as those call sites do. A mismatch refuses `ARBITER_ACCOUNT_CHANGED` or `REVIEWER_ACCOUNT_UNCONFIGURED`.
- Whether every `REVIEWER` completion event carries an int `version`. Check N counts an event at the head without one as newer (§2.2 step 12b case e). If A3 finds that legitimate events lack it, that is reported, never silently skipped.
- The exception type the store raises for a changed `record_sha256`. A3 (for `RULED`) and B1 (for `SUPERSEDED`) pin it with the real `TaskStore`. Any other exception reports `DISPUTE_RULE_WRITE_UNKNOWN` or `DISPUTE_SUPERSEDE_WRITE_UNKNOWN`, and the next call's fresh read decides.
- The completion-event write path used by A3's event-only race form. The PR names it, and it never bypasses a store check.
- The name of the existing dispute test file that A3b updates. The PR names it.
- That `task abandon` keeps `review_disputes` byte-equal (`task_store.py`, not read). B1 pins it. If it does not, that is a store defect: it is reported, and B1 does not merge.
- That `pm_caller.check_caller` (not read) refuses a caller that does not own a Task. B1 pins it for both Tasks, and a refusal writes nothing.
