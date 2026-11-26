# `#683` (`RD2` & `slice 2`) and `#2680 P1` Split Plan for `gw-agent`

This plan cuts the open `#683` items (`RD2` and `slice 2`, following `docs/design/gw-slices-683.md`) and the `#2680 P1` read-side review-claim and verdict-token unification into bounded pieces that `gw-agent` can execute within its file-range, `write_paths` (`<= 4` files), and context-window limits (`16,384` bytes at `T5`, `24,576` bytes at `T6`, including the task packet; no shell, no Git).

Every byte count and tier below is an **estimate** based on the `main` reference file sizes and targeted line-range reads.

---

## P1 — Reader Inventory (`#2680 P1`)

### Search Method & Scope

- **Search method:** Direct `read_file` inspection of every file in `read_paths` (all 17 files), recording every reader of `review_dispatch_claim`, `completion_events` (`source == 'REVIEWER'`), and stored verdict words (`PASS`, `FAIL`, `FIX`, `BLOCKED`, `RULING`, `REVIEW_PASS`, `REVIEW_FAIL`, `REVIEW_BLOCKED`).
- **Modules in `read_paths` with no `#2680 P1` readers (5):**
  - `docs/design/gw-slices-683.md`
  - `tools/llmtool/llmtool/chat_tui.py` (delegates to `chat_tui_status` and `chat_tui_events`)
  - `tools/llmtool/llmtool/chat_tui_pm.py`
  - `tools/llmtool/llmtool/pm_runtime.py`
  - `tools/llmtool/llmtool/pm_runtime_codex.py`
- **Modules NOT in `read_paths` (not searched):**
  - `tools/llmtool/llmtool/active_pm_inbox.py`
  - `tools/llmtool/llmtool/arbiter_recovery.py`
  - `tools/llmtool/llmtool/board_check.py`
  - `tools/llmtool/llmtool/board_check_part1.py`
  - `tools/llmtool/llmtool/board_check_part2.py`
  - `tools/llmtool/llmtool/dispute_arbiter.py`
  - `tools/llmtool/llmtool/high_debug.py`
  - `tools/llmtool/llmtool/lineage_counts.py`
  - `tools/llmtool/llmtool/review_repair_recovery.py`
  - `tools/llmtool/llmtool/task_brief.py`
  - `tools/llmtool/llmtool/task_completion.py`
  - `tools/llmtool/llmtool/task_completion_part2.py`
  - `tools/llmtool/llmtool/task_drive.py`
  - `tools/llmtool/llmtool/task_index.py`
  - All test files under `tools/llmtool/tests/`

### Inventory of Readers Found in `read_paths`

#### 1. `tools/llmtool/llmtool/task_status.py`
- `_summary`:
  - **Claim rule:** reads `review_dispatch_claim` (dict or list), excludes entries with `'purpose' in claim`, picks highest `version`.
  - **Event rule:** reads `completion_events` where `source == 'REVIEWER'`, checks `outcome == 'REVIEW_FAIL'` and `PASS`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, legacy `unknown`); Reviewer completion events.

#### 2. `tools/llmtool/llmtool/chat_tui_events.py`
- `_other_events`:
  - **Claim rule:** reads `review_dispatch_claim`, classifies `'purpose' in item` as `DISPUTE` and all others as `REVIEW`.
  - **Eligibility set:** all claims (`ruling` vs non-ruling display).
- `_completion_events`:
  - **Event rule:** reads `completion_events` (`source`, `state`, `outcome`).
  - **Eligibility set:** all completion events including `REVIEWER`.

#### 3. `tools/llmtool/llmtool/board_check_part3.py`
- `_classify_task`:
  - **Claim rule:** reads `bc._claims(record)`, excludes `'purpose' in item`, picks max `version`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`).

#### 4. `tools/llmtool/llmtool/review_transcript.py`
- `claim_usage`:
  - **Claim rule:** reads `review_dispatch_claim`, skips `'purpose' in claim`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`).
- `unposted_verdict`:
  - **Claim rule:** reads `review_dispatch_claim`, skips `'purpose' in claim`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`).
- `truncated_fail_repost`:
  - **Claim rule & verdict rule:** reads `review_dispatch_claim`, skips `'purpose' in claim`, checks `'Verdict: FAIL'` in text.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`), `FAIL` verdict header.
- `select_verdict`:
  - **Verdict rule:** calls `review_result._comment_verdict`, checks `'Verdict: FAIL'`.
  - **Eligibility set:** comment verdict tokens (`PASS`, `FAIL`, `FIX`, `BLOCKED`, `None`).

#### 5. `tools/llmtool/llmtool/chat_tui_status.py`
- `_formal_claim`:
  - **Claim rule:** reads `review_dispatch_claim`, filters `"purpose" not in claim`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`).
- `_repairing`:
  - **Claim rule:** reads `review_dispatch_claim`, filters `"purpose" not in r`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`).
- `reviewer`:
  - **Verdict rule:** reads `receipt['verdict']` or `_formal_claim` result verdict (`PASS`, `FAIL`, `FIX`, `BLOCKED`).
- `_reviewer_evidence`:
  - **Claim rule:** reads `_latest(record.get("review_dispatch_claim"))` without filtering `purpose` or `review_kind`.
- `detail_extra`:
  - **Claim rule:** dumps raw `review_dispatch_claim` list/dict for display.

#### 6. `tools/llmtool/llmtool/review_result.py`
- `review_kind`:
  - **Claim rule:** returns `'formal'` or `'security'` from `claim.get('review_kind')`, or `None`; does **not** inspect `'purpose'`.
- `_comment_verdict`:
  - **Verdict rule:** parses `PASS`, `FAIL`, `FIX`, `BLOCKED` from PR comment text.
- `diagnosis_review_body`:
  - **Claim & verdict rule:** filters `review_kind(claim) == 'formal'` and `verdict in ('FAIL', 'FIX')`.
  - **Eligibility set:** `formal` claims with `FAIL` or `FIX`.
- `_claim_for`:
  - **Claim rule:** reads `review_dispatch_claim`, matches `pr`, `executor_task`, `version`.
- `_settle_candidate`:
  - **Claim rule:** reads `review_dispatch_claim`, excludes `'purpose' in claim`.
  - **Eligibility set:** non-purposed review claims (`formal`, `security`, `unknown`).
- `_completed_claim_result`:
  - **Verdict rule:** validates `review_result['verdict'] in ('PASS', 'FIX', 'FAIL', 'BLOCKED')`.
- `_record_claude_claim_usage`:
  - **Claim rule:** reads `review_dispatch_claim`, excludes `'purpose' in claim`.
- `record_review_result`:
  - **Claim, verdict & event rule:** excludes `'purpose' in claim`, checks `kind == 'formal'`, maps verdict to `REVIEW_PASS` / `REVIEW_FAIL` / `REVIEW_BLOCKED`.

#### 7. `tools/llmtool/llmtool/task_completion_part1.py`
- `_newer_claim_pending`:
  - **Claim rule:** reads `review_dispatch_claim` without filtering `'purpose'` or `review_kind`.
  - **Eligibility set:** all claims.
- `review_handoff_record`, `review_return_decision_allowed`, `review_return_record`, `review_repair_intent`, `validate_review_dispatch_receipt`, `review_dispatch_blocked`, `is_formal_claude_pass`:
  - **Verdict rule:** check `verdict` against `PASS`, `FAIL`, `BLOCKED`, and `_m._REVIEW_VERDICTS`.

#### 8. `tools/llmtool/llmtool/review_repair.py`
- `_failed_event`:
  - **Claim, event & verdict rule:** filters `review_dispatch_claim` with `'purpose' not in item` (no `review_kind` check), reads `completion_events` (`source == 'REVIEWER'`, `outcome != 'REVIEW_FAIL'`), calls `review_result._comment_verdict`.
  - **Eligibility set:** non-purposed claims (`formal`, `security`, `unknown`); `REVIEWER` completion events.
- `claim_reviewer`:
  - **Claim rule:** reads `claim['review_result']['review_text']` to identify actor.

#### 9. `tools/llmtool/llmtool/review_dispatch.py`
- `_parse_codex_review_inner`:
  - **Verdict rule:** checks `verdict in ('PASS', 'FIX', 'FAIL', 'BLOCKED')` and calls `review_result._comment_verdict`.
- `_receipt_identity`:
  - **Claim rule:** checks `purpose == 'dispute_ruling'`.
- `_refuse_version_used`:
  - **Claim rule:** checks `'purpose' in claim` or `review_kind(claim) == kind`.
  - **Eligibility set:** kind-matched (`formal` or `security`) or purposed (`ruling`).
- `dispatch_review`:
  - **Claim rule:** checks `'purpose' in prior` and `review_kind(prior) != kind` / `review_kind(used) != kind`.
  - **Eligibility set:** `formal` or `security` matching dispatch `kind`.
- `dispatch_arbiter`:
  - **Claim & verdict rule:** writes/reads `purpose == 'dispute_ruling'` and `verdict` (`'RULING'` / `'BLOCKED'`).

#### 10. `tools/llmtool/llmtool/task_advance.py`
- `_claims`: reads raw `review_dispatch_claim`.
- `_is_ruling_claim`: checks `'purpose' in claim` or `_RULING_MARKER` on `review_result['review_text']`.
- `_non_ruling_claims`: filters `_claims` with `not _is_ruling_claim(claim)`.
- `_merge_pass_claim`: checks `review_kind == 'formal'`, `verdict == 'PASS'`, `REVIEW_PASS`.
- `_merge_security_claim`: checks `'purpose' not in claim`, `review_kind == 'security'`, `verdict in ('PASS', 'FIX', 'FAIL', 'BLOCKED')`, `REVIEW_PASS` / `REVIEW_FAIL` / `REVIEW_BLOCKED`.
- `_head_review_claim`, `_review_refusal_rerun`, `_merge_once`, `decide`, `_blocked_fallback_reviewer`, `_review_outcomes`, `_merged_review_target`: read `_claims` / `_non_ruling_claims` and `completion_events` (`REVIEW_PASS` / `REVIEW_FAIL` / `REVIEW_BLOCKED`).

#### 11. `tools/llmtool/llmtool/task_store.py`
- `_validate`: validates `review_dispatch_claim` (`purpose`, `review_kind`, `verdict` words), `completion_events`, `review_disputes`.
- `_normalized_review_claims`: normalizes `review_dispatch_claim` (dict or list).
- `_matching_arbiter_claims`, `_valid_arbitrate_ruling_binding`, `_arbiter_claim_transition_matches`: read `review_dispatch_claim` with `purpose == 'dispute_ruling'` and `verdict == 'RULING'`.
- `record_completion_event`, `_abandon_acked_events`, `unacked_completion_events`, `_update_record`: read `completion_events` and `review_dispatch_claim` transitions (`purpose`, `review_kind`).

#### 12. `tools/llmtool/llmtool/task_submission_cli.py`
- `_is_ruling_claim`: checks `'purpose' in claim` or `_RULING_MARKER` in `review_text`.
- `_dispute_ruling_claim`: filters `review_dispatch_claim` by `reviewer_task` / `native_session_id`, checks `purpose == 'dispute_ruling'`, `state == 'COMPLETED'`, `verdict == 'RULING'`.
- `_dispute_review_claims`: filters `review_dispatch_claim` by `pr` and optional `head_sha` (all claims).
- `_dispute_fail_verdict`: filters `_dispute_review_claims` with `not _is_ruling_claim(claim)`, `state == 'COMPLETED'`, `verdict in ('FAIL', 'FIX')`.
- `_dispute_rule_verified`: filters `_dispute_review_claims` with `not _is_ruling_claim(item)`, `state == 'COMPLETED'`.
- `_check_newer_formal_outcome`: reads `review_dispatch_claim` (skips `'purpose' in item`, checks `state == 'COMPLETED'`, `verdict in ('FAIL', 'FIX')`) and `completion_events` (`source == 'REVIEWER'`).
- `_ruling_check`, `_arbitrate_step_12`, `_replay_arbiter_claim`, `dispute_arbitrate`: filter `purpose == 'dispute_ruling'` and check `verdict` (`'RULING'`, `'BLOCKED'`).
- `_claude_review_claim_matches`: filters `not _is_ruling_claim(claim)` and `state == 'COMPLETED'`.
- `_formal_reviewed_heads`: filters `review_dispatch_claim` (`'purpose' not in claim` and `claim.get('review_kind', 'formal') == 'formal'`, `state in ('DISPATCHED', 'COMPLETED')`) and `completion_events` (`source == 'REVIEWER'`, `review_kind == 'formal'`).
- `_formal_pass_at_repair_head`: filters `not _is_ruling_claim(claim)`, `review_result.review_kind(claim) == 'formal'`, `state == 'COMPLETED'`, `verdict == 'PASS'`.

---

## P2 — `gw-agent` Pieces (`#2680 P1` & `#683`)

To keep every migration piece within `T5` (`16,384` B) or `T6` (`24,576` B), `#2680 P1` first introduces a tiny dedicated helper module `tools/llmtool/llmtool/review_claims.py` (`~2.5 KiB` estimated) instead of adding to `review_result.py` (`25,672` B). Each subsequent piece reads only `review_claims.py`, the target function's line range, and the target test's line range, changing **one function and its test** per piece without altering any gate decision, stored word, write event, or store schema.

### Execution Order & Dependencies

1. **Piece `2680-0` (Shared helpers):** Create `tools/llmtool/llmtool/review_claims.py` (`classify_review_claim`, `classify_verdict_token`) and `tools/llmtool/tests/test_review_claims.py`.
   - **Files:** `tools/llmtool/llmtool/review_claims.py` (new), `tools/llmtool/tests/test_review_claims.py` (new).
   - **Functions:** `classify_review_claim` (returns `'formal'`, `'security'`, `'ruling'`, `'unknown'`), `classify_verdict_token` (returns `'PASS'`, `'FAIL'`, `'FIX'`, `'BLOCKED'`, `'unknown'`).
   - **Estimated read bytes & tier:** ~4,000 B (packet + `review_result.review_kind` range); **T5** (`NEW_FILE_MIN_TIER = 4`).
   - **Dependencies:** None.
   - **Acceptance:** `classify_review_claim` classifies `dispute_ruling` / `_RULING_MARKER` as `'ruling'`, `review_kind == 'security'` (without `purpose`) as `'security'`, `review_kind == 'formal'` or absent (without `purpose`) as `'formal'`, and anything else as `'unknown'`; `classify_verdict_token` maps exact `PASS`/`FAIL`/`FIX`/`BLOCKED` and returns `'unknown'` otherwise.

2. **Piece `2680-1` (`task_status._summary`):** Migrate `task_status._summary` to `classify_review_claim` + `classify_verdict_token`.
   - **Files:** `tools/llmtool/llmtool/task_status.py`, `tools/llmtool/tests/test_task_status.py`.
   - **Functions:** `_summary` and its test in `test_task_status.py`.
   - **Estimated read bytes & tier:** ~12,500 B (`review_claims.py` ~2,500 B + `task_status.py` range ~5,000 B + test range ~2,500 B + packet ~2,500 B); **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves exact eligibility (`{'formal', 'security', 'unknown'}` excluding `'ruling'`/purposed) and verdict summary output; passes `test_task_status.py`.

3. **Piece `2680-2` (`chat_tui_events._other_events`):** Migrate `chat_tui_events._other_events` to `classify_review_claim`.
   - **Files:** `tools/llmtool/llmtool/chat_tui_events.py`, `tools/llmtool/tests/test_chat_tui_events.py`.
   - **Functions:** `_other_events` and its test in `test_chat_tui_events.py`.
   - **Estimated read bytes & tier:** ~11,000 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves `DISPUTE` vs `REVIEW` event classification (`'ruling'` -> `DISPUTE`, others -> `REVIEW`); passes `test_chat_tui_events.py`.

4. **Piece `2680-3` (`board_check_part3._classify_task`):** Migrate `board_check_part3._classify_task` to `classify_review_claim`.
   - **Files:** `tools/llmtool/llmtool/board_check_part3.py`, `tools/llmtool/tests/test_board_check.py`.
   - **Functions:** `_classify_task` and its test in `test_board_check.py`.
   - **Estimated read bytes & tier:** ~13,000 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves non-ruling eligibility (`{'formal', 'security', 'unknown'}`) and classification output; passes `test_board_check.py`.

5. **Piece `2680-4` (`review_transcript.claim_usage`):** Migrate `review_transcript.claim_usage` to `classify_review_claim`.
   - **Files:** `tools/llmtool/llmtool/review_transcript.py`, `tools/llmtool/tests/test_review_transcript.py`.
   - **Functions:** `claim_usage` and its test.
   - **Estimated read bytes & tier:** ~10,500 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves non-purposed eligibility (`{'formal', 'security', 'unknown'}`); passes `test_review_transcript.py`.

6. **Piece `2680-5` (`review_transcript.unposted_verdict`):** Migrate `review_transcript.unposted_verdict` to `classify_review_claim`.
   - **Files:** `tools/llmtool/llmtool/review_transcript.py`, `tools/llmtool/tests/test_review_transcript.py`.
   - **Functions:** `unposted_verdict` and its test.
   - **Estimated read bytes & tier:** ~10,500 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves non-purposed eligibility; passes `test_review_transcript.py`.

7. **Piece `2680-6` (`review_transcript.truncated_fail_repost`):** Migrate `review_transcript.truncated_fail_repost` to `classify_review_claim` + `classify_verdict_token`.
   - **Files:** `tools/llmtool/llmtool/review_transcript.py`, `tools/llmtool/tests/test_review_transcript.py`.
   - **Functions:** `truncated_fail_repost` and its test.
   - **Estimated read bytes & tier:** ~11,500 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves non-purposed eligibility and `FAIL` check; passes `test_review_transcript.py`.

8. **Piece `2680-7` (`chat_tui_status._formal_claim`):** Migrate `chat_tui_status._formal_claim` to `classify_review_claim`.
   - **Files:** `tools/llmtool/llmtool/chat_tui_status.py`, `tools/llmtool/tests/test_chat_tui_status.py`.
   - **Functions:** `_formal_claim` and its test.
   - **Estimated read bytes & tier:** ~11,000 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves existing non-purposed selection; passes `test_chat_tui_status.py`.

9. **Piece `2680-8` (`chat_tui_status._repairing`):** Migrate `chat_tui_status._repairing` to `classify_review_claim`.
   - **Files:** `tools/llmtool/llmtool/chat_tui_status.py`, `tools/llmtool/tests/test_chat_tui_status.py`.
   - **Functions:** `_repairing` and its test.
   - **Estimated read bytes & tier:** ~11,000 B; **T5**.
   - **Dependencies:** `2680-0`.
   - **Acceptance:** Preserves existing non-purposed repair-status detection; passes `test_chat_tui_status.py`.

10. **Piece `2680-9` (`review_result.diagnosis_review_body`):** Migrate `review_result.diagnosis_review_body` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/review_result.py`, `tools/llmtool/tests/test_review_result.py`.
    - **Functions:** `diagnosis_review_body` and its test.
    - **Estimated read bytes & tier:** ~12,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `{'formal'}` and `{'FAIL', 'FIX'}` eligibility; passes `test_review_result.py`.

11. **Piece `2680-10` (`review_result._settle_candidate`):** Migrate `review_result._settle_candidate` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/review_result.py`, `tools/llmtool/tests/test_review_result.py`.
    - **Functions:** `_settle_candidate` and its test.
    - **Estimated read bytes & tier:** ~11,500 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves non-ruling eligibility; passes `test_review_result.py`.

12. **Piece `2680-11` (`review_result._completed_claim_result`):** Migrate `review_result._completed_claim_result` to `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/review_result.py`, `tools/llmtool/tests/test_review_result.py`.
    - **Functions:** `_completed_claim_result` and its test.
    - **Estimated read bytes & tier:** ~11,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `{'PASS', 'FIX', 'FAIL', 'BLOCKED'}` acceptance; passes `test_review_result.py`.

13. **Piece `2680-12` (`review_result._record_claude_claim_usage`):** Migrate `review_result._record_claude_claim_usage` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/review_result.py`, `tools/llmtool/tests/test_review_result.py`.
    - **Functions:** `_record_claude_claim_usage` and its test.
    - **Estimated read bytes & tier:** ~11,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves non-ruling eligibility; passes `test_review_result.py`.

14. **Piece `2680-13` (`review_result.record_review_result`):** Migrate `review_result.record_review_result` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/review_result.py`, `tools/llmtool/tests/test_review_result.py`.
    - **Functions:** `record_review_result` and its test.
    - **Estimated read bytes & tier:** ~15,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `{'formal'}` vs `'security'` handling and `REVIEW_PASS`/`REVIEW_FAIL`/`REVIEW_BLOCKED` mapping; passes `test_review_result.py`.

15. **Piece `2680-14` (`review_repair._failed_event`):** Migrate `review_repair._failed_event` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/review_repair.py`, `tools/llmtool/tests/test_review_repair.py`.
    - **Functions:** `_failed_event` and its test.
    - **Estimated read bytes & tier:** ~13,500 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves non-ruling claim eligibility and `REVIEWER` completion-event filtering; passes `test_review_repair.py`.

16. **Piece `2680-15` (`review_dispatch._refuse_version_used`):** Migrate `review_dispatch._refuse_version_used` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/review_dispatch.py`, `tools/llmtool/tests/test_review_dispatch.py`.
    - **Functions:** `_refuse_version_used` and its test.
    - **Estimated read bytes & tier:** ~12,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves kind-matched + `'ruling'` collision check; passes `test_review_dispatch.py`.

17. **Piece `2680-16` (`review_dispatch.dispatch_review`):** Migrate `review_dispatch.dispatch_review` prior-claim checks to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/review_dispatch.py`, `tools/llmtool/tests/test_review_dispatch.py`.
    - **Functions:** `dispatch_review` and its test.
    - **Estimated read bytes & tier:** ~19,500 B (large function range + test range + packet); **T6**.
    - **Dependencies:** `2680-0`, `2680-15`.
    - **Acceptance:** Preserves `kind` matching and `'ruling'` exclusion; passes `test_review_dispatch.py`.

18. **Piece `2680-17` (`task_advance._is_ruling_claim`):** Migrate `task_advance._is_ruling_claim` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/tests/test_task_advance.py`.
    - **Functions:** `_is_ruling_claim` and its test.
    - **Estimated read bytes & tier:** ~11,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `'ruling'` detection (`purpose` or `_RULING_MARKER`); passes `test_task_advance.py`.

19. **Piece `2680-18` (`task_advance._merge_pass_claim`):** Migrate `task_advance._merge_pass_claim` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/tests/test_task_advance.py`.
    - **Functions:** `_merge_pass_claim` and its test.
    - **Estimated read bytes & tier:** ~13,500 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `{'formal'}` and `{'PASS'}` eligibility; passes `test_task_advance.py`.

20. **Piece `2680-19` (`task_advance._merge_security_claim`):** Migrate `task_advance._merge_security_claim` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/tests/test_task_advance.py`.
    - **Functions:** `_merge_security_claim` and its test.
    - **Estimated read bytes & tier:** ~13,500 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `{'security'}` and `{'PASS', 'FIX', 'FAIL', 'BLOCKED'}` eligibility; passes `test_task_advance.py`.

21. **Piece `2680-20` (`task_submission_cli._is_ruling_claim`):** Migrate `task_submission_cli._is_ruling_claim` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
    - **Functions:** `_is_ruling_claim` and its test.
    - **Estimated read bytes & tier:** ~11,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `'ruling'` detection (`purpose` or `_RULING_MARKER`); passes `test_task_submission_cli.py`.

22. **Piece `2680-21` (`task_submission_cli._dispute_fail_verdict`):** Migrate `task_submission_cli._dispute_fail_verdict` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
    - **Functions:** `_dispute_fail_verdict` and its test.
    - **Estimated read bytes & tier:** ~12,500 B; **T5**.
    - **Dependencies:** `2680-0`, `2680-20`.
    - **Acceptance:** Preserves non-ruling eligibility and `{'FAIL', 'FIX'}` check; passes `test_task_submission_cli.py`.

23. **Piece `2680-22` (`task_submission_cli._check_newer_formal_outcome`):** Migrate `task_submission_cli._check_newer_formal_outcome` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
    - **Functions:** `_check_newer_formal_outcome` and its test.
    - **Estimated read bytes & tier:** ~13,500 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves non-ruling eligibility and `{'FAIL', 'FIX'}` check; passes `test_task_submission_cli.py`.

24. **Piece `2680-23` (`task_submission_cli._formal_reviewed_heads`):** Migrate `task_submission_cli._formal_reviewed_heads` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
    - **Functions:** `_formal_reviewed_heads` and its test.
    - **Estimated read bytes & tier:** ~13,000 B; **T5**.
    - **Dependencies:** `2680-0`.
    - **Acceptance:** Preserves `{'formal'}` eligibility on claims and `REVIEWER` completion events; passes `test_task_submission_cli.py`.

25. **Piece `2680-24` (`task_submission_cli._formal_pass_at_repair_head`):** Migrate `task_submission_cli._formal_pass_at_repair_head` to `classify_review_claim` + `classify_verdict_token`.
    - **Files:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
    - **Functions:** `_formal_pass_at_repair_head` and its test.
    - **Estimated read bytes & tier:** ~13,500 B; **T5**.
    - **Dependencies:** `2680-0`, `2680-20`.
    - **Acceptance:** Preserves `{'formal'}` and `{'PASS'}` eligibility; passes `test_task_submission_cli.py`.

26. **Piece `2680-25` (`task_submission_cli._claude_review_claim_matches`):** Migrate `task_submission_cli._claude_review_claim_matches` to `classify_review_claim`.
    - **Files:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
    - **Functions:** `_claude_review_claim_matches` and its test.
    - **Estimated read bytes & tier:** ~12,000 B; **T5**.
    - **Dependencies:** `2680-0`, `2680-20`.
    - **Acceptance:** Preserves non-ruling eligibility; passes `test_task_submission_cli.py`.

### `#683` `RD2` Conditional Split Assessment

- Per `docs/design/gw-slices-683.md`, `#683 RD2` (`CodexRuntime._launch` / `_events` in `pm_runtime_codex.py`, `PMRuntime._finish` / `_persist_turn` in `pm_runtime.py`, `usage_totals` in `chat_tui_pm.py`) is currently **BLOCKED (`UNKNOWN`)** awaiting one real `codex exec resume --json` `turn.completed` payload.
- Once unblocked by that payload, the `pm_runtime_codex.py` (`18,939` B) and `chat_tui_pm.py` (`18,430` B) changes can each be dispatched as a `T6` single-function piece (`CodexRuntime._events` and `usage_totals` with targeted test ranges), whereas `PMRuntime._finish` / `_persist_turn` in `pm_runtime.py` (`74,565` B) + `test_pm_runtime.py` is listed in P3 as `beyond_t6` unless narrowed to a single-function range under `24,576` B.

---

## P3 — `beyond_t6` Items & Owner Acceptance

| Item | Files & Functions | Estimated Size | Reason Beyond `T6` (`24,576` B) / Blocker | Suggested Route | Owner Acceptance |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `#683` Slice 2 (`WP-1161b`) | `chat_tui.py` (`status_report`, `pm_status_fields`), `chat_tui_pm.py` (`usage_totals`, `usage_gaps`, `usage_status_line`), `tests/test_chat_tui.py` | ~`783,217` B total (`chat_tui.py` `239,587` B + `test_chat_tui.py` `525,200` B) | Both `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES` (`2000`) and `T6` (`24,576` B) when coordinated status/test context is needed. | `opencode` or `agy` | **Required** (per `docs/design/gw-slices-683.md`) |
| `#683` `RD2` (combined / `pm_runtime.py` turn persistence) | `pm_runtime_codex.py` (`_launch`, `_events`), `pm_runtime.py` (`_finish`, `_persist_turn`), `chat_tui_pm.py` (`usage_totals`), `tests/test_pm_runtime.py` | `pm_runtime.py` `74,565` B + `pm_runtime_codex.py` `18,939` B + `chat_tui_pm.py` `18,430` B | Currently **blocked (`UNKNOWN`)** pending one real `codex exec resume --json` `turn.completed` payload; full cross-module + test context exceeds `T6`. | `opencode` or `agy` (after payload observed) | Not marked in `gw-slices-683.md` (check PM intake) |
| `#2680 P1` `task_store._validate` & arbiter claim transitions | `task_store.py` (`_validate`, `_matching_arbiter_claims`, `_valid_arbitrate_ruling_binding`, `_arbiter_claim_transition_matches`) | `task_store.py` > 7,200 lines | `_validate` and transition validators are tightly coupled to strict schema/transition invariants across > 2,000 lines; risky to split per-check without full validator context (> `24,576` B). | `opencode` or `agy` | No (internal refactor, zero gate/schema change) |
| `#2680 P1` `task_completion_part1` verdict-token readers | `task_completion_part1.py` (`_newer_claim_pending`, `review_handoff_record`, `review_return_decision_allowed`, `review_return_record`, `review_repair_intent`, `validate_review_dispatch_receipt`, `review_dispatch_blocked`, `is_formal_claude_pass`) | ~22,000 B module + test file | Shared receipt/handoff contract validation spans multiple coupled functions and tests; safer as one bounded cross-function pass above `T6`. | `opencode` or `agy` | No (internal refactor, zero gate/schema change) |
