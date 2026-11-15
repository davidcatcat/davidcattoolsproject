# Trusted dispute-arbiter path (#2087) and dispute supersede (#2088)

Status: **待實作** (design only; no code in this change).

Scope: docs only. This note designs two llmtool changes:

- **A (#2087):** a llmtool step that launches the arbiter itself and records its ruling. The disputing PM never names, binds or vouches for the arbiter run.
- **B (#2088):** a recorded `SUPERSEDED` transition for a dispute made moot by a corrected-contract replacement Task.

Binding Owner decision: ruling C on #1817. **The disputing PM must never bind or vouch for the arbiter.**

Source read for this note: `c0381925` (the Task source commit; it changes only this note over `main` at `9b08c2fe`, so the code line numbers are the same on both). Line numbers refer to that commit. Files outside this Task's read scope are marked UNKNOWN where they matter (`task_store.py`, `review_result.py`, `lineage_counts.py`, `pm_caller.py`, `scripts/d6_worktree_lifecycle.py`, merge-gate code).

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
  - `dispatch_review` (`review_dispatch.py:1207-1504`) renders the formal review prompt (`run_task_review_prompt`, `:1349-1356`).
  - The Codex parser accepts only a `review_verdict` marker and a `Verdict:` header (`review_dispatch.py:377-385`). A `dispute_ruling` body is refused `REVIEW_HEADER_MISMATCH`.
  - The Claude route ends `DISPATCHED` (`:1406`); its later completion lives in `review_result.py` (outside read scope). Whether that path accepts ruling text is UNKNOWN.
  - So, as read, no trusted route produces the `COMPLETED` ruling claim that `dispute_rule` requires.
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
   - Load the `COMPLETED` claim with `version == entry.review_version` at that head.
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
6. **Rules.** Build `_review_rules(repo_root)` from authenticated `main` before any write, as at `review_dispatch.py:1317-1319`.
7. **Arbiter claim.**
   - One compare-and-update on `record_sha256` appends a `review_dispatch_claim` with these fields:
     - the fields every claim has (as at `review_dispatch.py:1321-1325`), which `_dispute_ruling_claim` and `_dispute_review_claims` read: `executor_task` (the Task's `completion.executor_task_id`), `pr`, `head_sha` (= `entry.head_sha`), `claimed_at`, `updated_at`;
     - `purpose: 'dispute_ruling'`;
     - `dispute: {pr, finding_id, head_sha, review_version}`;
     - `reviewer`: the arbiter key llmtool chose in step 4 (`claude-reviewer` or `codex-reviewer`). This is recorded because `claim_reviewer` cannot derive it from a ruling claim (§1.3 item 5);
     - `version`: max recorded version of this Executor and PR + 1, so it is always later than `entry.review_version`;
     - `reviewer_task`: `'reviewer-' + os.urandom(10).hex()`, generated by llmtool as at `:1320`;
     - `native_session_id: None`, `state: 'CLAIMED'`, `rules_sha256`, `account_mode`, and `account` when the role has one (`:1326-1327`).
   - The same write appends a history event (§2.6) and sets `entry.arbiter_claim = {reviewer_task, version}`.
   - If the entry already has an `arbiter_claim`, this is a replay (§2.4), not a new claim.
8. **D6.**
   - `_create_d6` (`review_dispatch.py:286-305`) creates the arbiter's read-only Reviewer D6 with `base_sha` = the disputed head and `--agent-id` = the chosen Reviewer key.
   - This step itself creates the registered D6 Reviewer record that `_dispute_arbiter_registration` later verifies.
   - The lease text names dispute arbitration of this Task, PR and finding.
   - `_dispute_arbiter_registration` requires the record's `result_sha` to equal the disputed head (`task_submission_cli.py:2555`). Whether `d6_worktree_lifecycle.py` sets `result_sha` to `--base-sha` for a Reviewer D6 is UNKNOWN (out of scope). PR A2 confirms it by test. If it does not, the ruling is refused `DISPUTE_ARBITER_UNVERIFIED` (fail-closed), and the lifecycle change is a separate PR (§8).
9. **Prompt.**
   - llmtool renders a fixed ruling prompt: rules prefix + the binding line + the disputed `Finding:` line and its section, taken verbatim from the stored claim `review_text`.
   - It adds the PM's dispute text, read live from `entry.evidence_ref`. That text is screened (`_screen_text`), size-limited and fenced as untrusted quoted data.
   - The prompt also gives the exact output contract (§2.3).
   - Before launch, preflight runs again. A moved head refuses `DISPUTE_STALE`, as at `review_dispatch.py:1372-1376`. The policy gate is re-read.
10. **Launch and bind.** Use the same calls as `dispatch_review`:
    - Codex: `_dispatch_codex` (`:644-683`) with a ruling parser (§2.3) in place of `_parse_codex_review`. The session comes from the `thread.started` event.
    - Claude: `_dispatch` (`:686-705`). The session comes from the bridge receipt `actual_session_id`.
    - Then `_bind_d6(reviewer_task, session_id, repo_root)` (`:708-733`). This is the existing trusted bind path through `d6_worktree_lifecycle.py --bind-native-session-task-id`.
    - The session id **only** ever comes from the runtime's own receipt, never from the PM.
11. **Claim result.**
    - Codex goes to `COMPLETED` with `review_result = {task_id, run_id, contract_digest, head, verdict: 'RULING', outcome, review_text}`.
    - Claude goes to `DISPATCHED`, and its completion is recorded by the existing Claude review-result path. PR A2 must confirm that this path (in `review_result.py`, UNKNOWN here) can carry a ruling body; if not, it adds a ruling branch there.
    - Screened or withheld text records `verdict: 'BLOCKED'` and empty text, as at `:1411-1419`. No ruling follows, and the dispute stays `OPEN`.
    - Any exception records `UNKNOWN` with the session if known, as at `:1431-1491`.
12. **Post and rule.** For a `COMPLETED` arbiter claim:
    - llmtool posts the ruling comment itself through `_ensure_verdict_posted` (`review_dispatch.py:1077-1094`), under the existing post receipt and lock. The receipt name is keyed by the arbiter claim (`_verdict_post_name(task_id, claim)`). The caller-posting hand-off (`_reserve_caller_post`, `:1049-1074`) is not used, so the PM never receives ruling text to post.
    - The evidence comment id is the one llmtool's own receipt records, not a URL from the PM. Only a post result of `POSTED` with `verified: True`, or `ALREADY_POSTED`, that carries an integer `comment_id` (`review_dispatch.py:1133-1134`, `:1166-1172`, `:1195-1199`) counts.
    - `IN_PROGRESS`, `POSTED_UNVERIFIED`, a result without a comment id, or a post failure (`VERDICT_POST_*`) reports `ARBITER_POST_PENDING` and writes no ruling. The dispute stays `OPEN`, and a later `arbitrate` replays step 12 under the same receipt (§2.4). It never posts a second time.
    - llmtool builds `evidence_ref` itself as the PR comment URL of the trusted slug (`_dispute_repository`, `task_submission_cli.py:2468-2481`), the PR and that comment id. The URL must pass `valid_review_dispute_ref`, as at `:2491`. The existing refusal of an `evidence_ref` equal to the entry's own (`:2831-2832`) stays.
    - llmtool then runs the existing verification as one internal function `_dispute_rule_verified(...)` factored out of `dispute_rule`: freshness (`:2842-2852`), `_dispute_arbiter_registration`, `_dispute_ruling_claim` and `_dispute_ruling_comment`. The checks stay as they are, with one addition and one change:
      - **Addition:** the claim found must be the entry's own `arbiter_claim` (same `reviewer_task` and `version`) with `purpose: 'dispute_ruling'`.
      - **Change, for `purpose: 'dispute_ruling'` claims only:** the Reviewer check at `:2638` reads the claim's recorded `reviewer` instead of `claim_reviewer(claim)`. It still requires that key to equal `arbiter['reviewer']`, and the ruling marker's actor (`Claude`/`Codex`) must map to the same key. Formal claims keep `claim_reviewer` unchanged. This is not a weaker check: the recorded `reviewer` is written by llmtool in step 7, never by the PM or the model, and the model's own actor must still agree.
    - The argument checks that `dispute_rule` applies today (`D6_TASK_ID` for the Reviewer Task and `valid_review_dispute_session` for the session, `:2815-2818`) also apply to the values from llmtool's own records. Whether `'reviewer-' + 20 hex` matches `D6_TASK_ID`, and whether a Codex `thread_id` or Claude `actual_session_id` passes `valid_review_dispute_session`, is UNKNOWN here (`task_store.py` is out of scope). PR A3 confirms both by test. If either check fails, A3 widens the pattern only to the exact shape llmtool generates, in its own reviewed change, and never removes the check (§8).
    - It passes `reviewer`, `reviewer_task`, `reviewer_session`, `outcome` and `evidence_ref` from its own records only. `outcome` is the one `parse_ruling` read from the arbiter's text (§2.3).
    - It writes `RULED` in one compare-and-update.

### 2.3 Ruling output contract

The arbiter's text must have these first three non-empty lines, as `_dispute_ruling_comment` already requires (`task_submission_cli.py:2656-2660`):

```
<!-- activity: type=dispute_ruling actor=<Claude|Codex> target=PM status=<UPHELD|WITHDRAWN> -->
Ruling: <F#> <UPHELD|WITHDRAWN> | PR: #<pr> | HEAD: <head>
LLMTOOL-REVIEW-BINDING v1 task=<task> run=<run> contract=<digest> head=<head>
```

The text must contain exactly one `Ruling:` line.

A new strict parser, `parse_ruling(text, *, actor, finding_id, pr, head, binding_line)`, enforces this. It reuses `_RULING_MARKER`, `_RULING_HEADER` and `_RULING_ANY_LINE` (`task_submission_cli.py:2575-2581`).

- The **outcome is parsed from the arbiter's text**. The PM never supplies it.
- An actor that is not the chosen arbiter's family refuses `DISPUTE_RULING_OUTPUT_INVALID`, as does a finding, PR or head that differs, or a missing binding line.
- For Codex, the JSONL envelope stays as in `_parse_codex_review_inner` (`review_dispatch.py:318-350`): one `thread.started`, one final `agent_message`. Only the inner object changes, to `{task_id, run_id, head, outcome, ruling}`.

### 2.4 Replay

`arbitrate` on an entry that already has `arbiter_claim` does not start a new run. It follows the claim's state, like the prior-claim branch of `dispatch_review` (`review_dispatch.py:1231-1262`):

| Arbiter claim state | Result |
|---|---|
| `CLAIMED` | becomes `UNKNOWN` (outcome uncertain; never replayed) |
| `DISPATCHED` | reports `ARBITER_PENDING`; no write |
| `COMPLETED`, not yet ruled | step 12 (post once under receipt, verify, rule); no usable comment id reports `ARBITER_POST_PENDING`, no write |
| `COMPLETED`, `BLOCKED` | reports `DISPUTE_RULING_WITHHELD`; dispute stays `OPEN` |
| `UNKNOWN` | reports `ARBITER_OUTCOME_UNKNOWN`; dispute stays `OPEN` |

- A dispute whose arbiter claim is `UNKNOWN` or `BLOCKED` stays `OPEN` and keeps blocking.
- A second arbiter run is an Owner decision relayed by the PM. It would need its own later design; this note gives no retry path.

### 2.5 Retire the manual `rule` path

- `task dispute rule` keeps its name, so old scripts fail clearly. It refuses `DISPUTE_RULE_MANUAL_RETIRED` before reading any argument value.
- The `--reviewer`, `--reviewer-task`, `--reviewer-session` and `--outcome` options are removed.
- The verification code stays and is reached only from `arbitrate` step 12.

### 2.6 Recorded evidence

The `RULED` entry's `ruling` dict keeps today's keys (`task_submission_cli.py:2864-2866`) and adds:

- `path: 'arbitrate'`;
- `arbiter_claim_version`, `rules_sha256`;
- `review_text_sha256`: SHA-256 of the exact UTF-8 ruling text;
- `claim_sha256`: SHA-256 of the canonical JSON of the `COMPLETED` arbiter claim;
- `comment_id`: from llmtool's own post receipt;
- `contract_digest`, `run_id`.

Each dispute entry gets an append-only `history` list. Every transition appends one event:

```
{event, at, actor, head_sha, refs}
```

- `event` is one of `OPENED`, `DEFERRED`, `ARBITER_CLAIMED`, `ARBITER_DISPATCHED`, `ARBITER_COMPLETED`, `ARBITER_UNKNOWN`, `ARBITER_WITHHELD`, `RULED`, `SUPERSEDED`.
- `actor` is `pm:<caller session>` for PM-requested steps and `llmtool` for steps llmtool performs.
- `refs` holds fixed ids and digests only: never model text, never a URL from model text.
- `task_store.py` must refuse a write whose `history` is not a strict extension of the stored one. That validation is outside this note's read scope; PR A1 adds it.

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
   - A `purpose: 'dispute_ruling'` claim is never a formal verdict. `_dispute_fail_verdict` and the formal readers exclude it by `purpose` as well as by text marker (`_is_ruling_claim`, `task_submission_cli.py:2585-2592`).
   - Whether lineage counting counts it as a review round is UNKNOWN here. PR A1 pins whichever it is by test, and it may only add to a count, never lower one.
6. **A ruling never replaces the formal PASS.** `formal_pass_required: True` stays (`task_submission_cli.py:2873`).
7. **Every transition is audited** in the append-only entry `history` (§2.6). The store refuses rewriting it.
8. **Terminal states.** `RULED` and `SUPERSEDED` are terminal. No transition leaves them.

---

## 5. Threats

| Threat | Defence |
|---|---|
| **Forged session.** PM or another agent presents a session of its choosing. | No option accepts a session. The session comes only from the runtime receipt (`thread.started` / `actual_session_id`), and `_bind_d6` binds it. `_dispute_arbiter_registration` requires the registration's `native_session_id` to equal it (`:2561-2563`). Developer sessions and the first-review session are excluded (`:2564-2571`, `:2850`). |
| **Forged Reviewer D6.** A D6 created by hand at the head. | llmtool generates `reviewer_task` at claim time and records it on the entry before the D6 exists. Verification uses only that id; any other D6 is never consulted. |
| **Forged evidence comment.** All agents share one GitHub login (#926), so someone posts a byte-equal comment. | The comment id comes from llmtool's own post receipt, not a URL argument. The poster's duplicate check (`_find_posted_verdict`, `review_dispatch.py:1018-1041`) refuses a second exact copy. `_dispute_ruling_comment` still requires an unedited comment posted after `opened_at`. |
| **Wrong head.** The PR moves between open, launch and ruling. | Live head checked at steps 2, 5, 9 and 12. A move refuses `DISPUTE_STALE`. The entry stays `OPEN` on the old head; the history keeps the arbiter claim. |
| **Replay of an old ruling.** A ruling from another dispute, PR, Task or run. | The ruling header binds finding, PR and head. The binding line binds task, run and contract. The claim binds `dispute.{pr, finding_id, head_sha, review_version}`. One arbiter claim per entry; `RULED` is terminal. |
| **Replay of `arbitrate`.** It is called twice. | The second call follows the recorded claim (§2.4). A CAS conflict on `record_sha256` refuses the loser before any D6 or launch. |
| **Race with a new formal review.** A new formal review takes the same version, or replays the arbiter claim. | Versions are allocated under the record CAS. `dispatch_review`'s prior-claim branch (`review_dispatch.py:1231-1262`) must refuse `REVIEW_DISPATCH_VERSION_USED` for a `purpose: 'dispute_ruling'` claim, so it never posts ruling text as a verdict. A newer formal verdict at the same head refuses `DISPUTE_VERDICT_NEWER`. |
| **Race supersede vs arbitrate.** Both run at once. | Both write through CAS on the old Task. Whichever commits second re-reads and refuses (`DISPUTE_SUPERSEDED` or `DISPUTE_RULING_PENDING` / `DISPUTE_ALREADY_RULED`). |
| **Supersede used to escape a ruling.** | A pending `COMPLETED` ruling refuses supersede. Supersede lifts no refusal and withdraws no finding. |
| **Forged replacement.** A Task that does not really replace the old one. | The checks are: `continues` equals the old Task exactly, same chain key under the chain lock, a different contract digest, and a registered, clean Executor D6 with a new head. |
| **Prompt injection in the PM's dispute text.** | The text is screened, size-limited and fenced as untrusted data. The output contract is fixed and parsed strictly; anything else is `BLOCKED` and no ruling. |
| **Sensitive data in ruling text.** | `_screen_text` before storing and posting. Withheld text is never posted (as `review_dispatch.py:1495-1498`). |

---

## 6. Fail-closed cases (summary)

- **`arbitrate`:**
  - caller not owner, bad arguments, abandoned Task;
  - no `OPEN` entry at the live head; stale entry; superseded entry; newer formal verdict;
  - arbiter unavailable or not allowed by policy; family clash;
  - Executor D6 dirty or unregistered; PR head unreadable or moved;
  - rules from `main` unreadable;
  - claim CAS conflict; D6 create failure; launch failure; bind failure or timeout;
  - ruling output invalid or withheld; post receipt unavailable, in progress, unverified or without a comment id; any verification refusal;
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
- runtime, bind, GitHub and screen functions are injected or monkeypatched (`dispatch_session`, `bind_session`, `codex_runner`, `comment_poster`, `comment_lister`, `current_head`, `github_get`, `_screen_text`), and the real behaviour is still checked;
- no production check is weakened.

The PM runs the tests on EC2; none were run for this note.

### PR A1: claim purpose and store schema (#2087)

Files:

- `tools/llmtool/llmtool/task_store.py`: `purpose`/`dispute`/`reviewer` keys on `review_dispatch_claim` (`reviewer` allowed only with `purpose: 'dispute_ruling'`); `arbiter_claim` and append-only `history` on dispute entries; the terminal `SUPERSEDED` state and its `superseded` key for B1, so B1 stays at four files.
- `tools/llmtool/llmtool/review_dispatch.py`: the prior-claim branch refuses a `dispute_ruling` claim.
- `tools/llmtool/llmtool/task_submission_cli.py`: `_dispute_fail_verdict` also excludes by `purpose`.
- `tools/llmtool/tests/test_dispute_claim_purpose.py`.

Tests:

- The store refuses a `history` that is shortened or rewritten, an unknown `purpose`, and a `reviewer` key on a claim without `purpose: 'dispute_ruling'`.
- `dispatch_review` with the version of a `dispute_ruling` claim refuses `REVIEW_DISPATCH_VERSION_USED` and posts nothing.
- A `dispute_ruling` claim is never picked as the disputed FAIL verdict.
- Counting with an arbiter claim present never lowers any count.

### PR A2: `arbitrate` launch (#2087)

Files:

- `tools/llmtool/llmtool/dispute_arbiter.py` (new): ruling prompt render and `parse_ruling`.
- `tools/llmtool/llmtool/review_dispatch.py`: `dispatch_arbiter` reusing `_preflight`, `_create_d6`, `_dispatch`, `_dispatch_codex` (inner parser swapped) and `_bind_d6`.
- `tools/llmtool/llmtool/task_submission_cli.py`: `arbitrate` parser and adapter, steps 1-11.
- `tools/llmtool/tests/test_dispute_arbitrate_launch.py`.

If `review_result.py` needs a ruling branch for the Claude route (§8), that change is a separate PR **A2b**: `review_result.py` plus `tools/llmtool/tests/test_dispute_arbitrate_claude_completion.py`. Its tests: a Claude ruling body completes the arbiter claim, and a formal-verdict body on an arbiter claim (or a ruling body on a formal claim) is refused. A2 then ships Codex end to end, and the Claude route reports `ARBITER_PENDING` until A2b merges.

Tests:

- The parser has no reviewer, session, outcome or evidence option, and passing one exits as a usage error.
- llmtool chooses the arbiter (family matrix), and a disallowed policy refuses before any write.
- One claim per entry. A replay in each state follows §2.4, and `CLAIMED` becomes `UNKNOWN`.
- The stored arbiter claim carries `executor_task`, `pr`, `head_sha`, `purpose`, `dispute`, the llmtool-chosen `reviewer` and a version later than the disputed one.
- The arbiter Reviewer D6 is created at the disputed head with the chosen Reviewer key. Its registration `result_sha` equals that head, which pins the §8 point either way.
- A head move before launch refuses `DISPUTE_STALE` with no launch.
- The session recorded is the one the injected runtime returned, and bind is called with it.
- A ruling with the wrong actor, finding, head or binding line refuses `DISPUTE_RULING_OUTPUT_INVALID`.
- A screen refusal (monkeypatched `_screen_text`) records `BLOCKED`, and the dispute stays `OPEN`.

### PR A3: post, rule, retire manual rule (#2087)

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`: `_dispute_rule_verified`; step 12; `rule` refuses `DISPUTE_RULE_MANUAL_RETIRED`; `ruling` evidence digests.
- `tools/llmtool/llmtool/review_dispatch.py`: post receipt keyed by arbiter claim; returns comment id.
- `tools/llmtool/tests/test_dispute_arbitrate_rule.py`.
- `tools/llmtool/tests/test_review_dispute.py` (existing dispute tests, if present, updated for retirement only, with no check weakened). If that file has another name, the PR names it; UNKNOWN here.

Tests:

- A full arbitrate run with injected runtime and GitHub writes `RULED` with the evidence digests, for both a Claude arbiter (`account_mode` `ROLES` or `EXPLICIT`) and a Codex arbiter (`ROLES` and `CODEX_CLI`). This pins the §1.3 item 5 gap as closed.
- A ruling claim whose recorded `reviewer` differs from the chosen arbiter, or whose marker actor differs from the recorded `reviewer`, refuses `DISPUTE_RULING_CLAIM_INVALID`. A formal claim still goes through `claim_reviewer` unchanged.
- An arbiter claim missing `executor_task`, `pr` or `head_sha` refuses.
- A post result of `IN_PROGRESS`, `POSTED_UNVERIFIED` or one without a comment id reports `ARBITER_POST_PENDING` and writes nothing. The replay then rules without a second post (injected `comment_poster` called once).
- The `evidence_ref` passed to verification is built from the trusted slug and the receipt comment id. The PM-supplied value is never consulted.
- The generated `reviewer_task` and the injected runtime session pass the existing `D6_TASK_ID` / `valid_review_dispute_session` checks. The test pins the shape and keeps the check.
- `rule` with any arguments refuses `DISPUTE_RULE_MANUAL_RETIRED` and writes nothing.
- A duplicate exact comment refuses.
- An edited comment, or one posted before `opened_at`, refuses.
- A Developer session, the first-review session, or a D6 other than the claimed `reviewer_task` refuses.
- A head move between completion and ruling refuses `DISPUTE_STALE`.
- `formal_pass_required` stays true, and `withdrawn_findings` changes only after `RULED WITHDRAWN`.

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

- Whether `review_result.py` can complete a Claude-route ruling claim unchanged (UNKNOWN; decides whether PR A2b is needed).
- Whether `task_store.py` allows the one `review_disputes` write on an abandoned Task (UNKNOWN; §3.2 item 5).
- Whether lineage counting counts a `dispute_ruling` claim as a review round (UNKNOWN; A1 pins it, never lowering a count).
- Which merge-gate code reads `review_disputes` (UNKNOWN; B1 must cover every reader).
- Whether a Reviewer D6 made by `_create_d6` records `result_sha` equal to its `--base-sha` (UNKNOWN; `d6_worktree_lifecycle.py` is out of scope; A2 pins it, and any lifecycle change is a separate PR).
- Whether `'reviewer-' + 20 hex` matches `D6_TASK_ID` and runtime session ids pass `valid_review_dispute_session` (UNKNOWN; `task_store.py` is out of scope; A3 pins both; a needed change only widens a pattern to the exact generated shape, in its own reviewed PR).
