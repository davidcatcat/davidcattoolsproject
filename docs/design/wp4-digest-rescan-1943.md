# WP-4 design: digest-bound rescan of refused Developer output (#1943)

**Proposal only; not a review or acceptance.** Source main SHA: `2b1f4e2494efef85e22b7bfb7fbbe2cea0ad51b3` (the `main` commit merged into this branch); `path:line` is at that SHA, code under `tools/llmtool/llmtool/`. Unread is UNVERIFIED. Design: 待實作（WP-4）.

## 1. Today (現行)

**Screen.** `developer_diff_sensitive` runs the shared screen on added lines and releases only a proven plain-code Python keyword match. Errors keep the refusal (`developer_diff_screen.py:70-85`, `:36-39`). Callers in `task_submission_cli.py`:
- The strict check returns `DEVELOPER_OUTPUT_SENSITIVE` (`:3181-3182`). It is used by text normalization (`:3206`, `:3216`, `:3253`), the text route (`:7696-7702`) and the cumulative D6 diff with base-revision pre-images (`:3338-3340`, `:6967-6969`).
- OpenCode passes pre-images at the snapshot head (`:5036-5037`). AGY passes none (`:5456`).

**Record.** Written before the Task update (`task_submission_cli.py:3519-3522`): code, source `DEVELOPER_OUTPUT`, up to 32 `rule:line` hits, total, `omitted`, hit-list digest, byte count, SHA-256 of the screened output, `d6` (the completion's `executor_task_id`), `refusal_id`, and an append-only refusal event. No text (`:3432-3458`; `high_debug.py:471-511`); hits are rule names and lines (`task_submission_cli.py:3416-3429`). Redacted diagnostic captures are separate (`:3356-3399`).

**Block.** The delegate gate checks every Task on the same executor D6 (`:7264-7270`). Any stored disposition clears it (`:3504`):
- `FALSE_POSITIVE` from `screen-disposition` (`high_debug.py:640-671`), which checks only the stored hits (`:664-665`): no digest, D6 or history check;
- `BATCH_CONFIRMED` from overflow recovery (`:841-845`, `:903-904`), whatever the batch classifications (`:737`), all-`FALSE_POSITIVE` included.

**screen-recover** (`high_debug.py:794-908`, CLI `:1590-1649`) is for overflow records only (`:811-813`). It binds `d6` by name only (`:497`, `:814-815`), rebuilds the output from the D6 against `developer.source_sha` (`task_submission_cli.py:3461-3478`, `high_debug.py:767-783`), checks the digest (`:816-820`) and history (`:697-756`, `:828-831`), and requires the **same** hits, else `HIGH_DEBUG_SCREEN_HITS_MISMATCH` (`:825-827`).

**Gaps against Route 2:**
- G1. screen-recover proves the refusal is unchanged; it cannot express "no hit".
- G2. It rescans with the hit locator (`high_debug.py:759-764`, `task_submission_cli.py:3416-3429`), which lacks the Python release, not the gate decision.
- G3. `FALSE_POSITIVE` and `BATCH_CONFIRMED` release the D6 block (`:3504`); Route 2 says no opinion releases it.
- G4. The block is keyed to the old D6 (`:7264-7270`); a guard against a second new Task on a new D6 is UNVERIFIED, so the limit is manual.
- G5. AGY diffs against `snapshot.head` (`:5449`), the rebuild against `developer.source_sha` (`:3475`); equality and `task_completion.py` (not read) are UNVERIFIED.
- G6. A text-route refusal digests the model message (`:7699-7702`), which no D6 holds. Empty-diff capture is diagnostic only (`:6973-6982`): its code is `DEVELOPER_OUTPUT_INVALID_ENVELOPE` (`:3169-3170`) or an ignored-file code (`:6975-6976`), and only `DEVELOPER_OUTPUT_SENSITIVE` writes a record (`:3519-3522`).

## 2. Requirements (quoted, `skills/ai-team-delivery/pm-continuation.md`)

`:61`:
> Never pass refused content to a new Developer, never resend the old Task and never hint how to pass the screen.

> Start at most one new independent Developer Task per blocked item, from latest main. If that Task is refused again, stop and create no third

`:63`, `:67-68`, `:70`:
> Fixing the screen and releasing the refused item are separate.

> **Route 2, the refused item:** it stays blocked, and no Reviewer opinion releases it. It may restart only when all of these hold: the screen fix is merged; the fixed screen is installed and verified; the original source still exists; its digest matches the recorded digest exactly; the fixed screen rescans that same source with no hit.

> Then at most one new compliant Task starts from latest `main` under the rules above. A missing source, a digest mismatch, a hit or incomplete verification keeps it blocked. A second screen fix in the same rule family is an Owner question (`R-SECURITY`).

> Never resend refused content, rewrite sensitive data to pass the screen, or fabricate a Developer response that can no longer be retrieved.

## 3. Design (待實作（WP-4）)

### 3.1 Record binding

New records gain five metadata keys; the reader accepts legacy, current and new key sets (`high_debug.py:64-67`, `:522-523`): `base_sha` (the gate's diff base), `d6_branch` and `d6_head` (D6 registration and snapshot), `screen_sha256` (installed screen code, Q2) and `screen_mode` (pre-images given: `none`, `base_revision` or `snapshot_head`). A record missing any is **legacy** for Route 2 (Q3).

### 3.2 `screen-rescan`

```text
python -m llmtool.high_debug screen-rescan --task-id <T> --family developer-output
    --d6 <executor D6> --fix-sha <Route 1 merge SHA> --ref <Issue comment URL>
```

Steps run in order. Steps 1-7 write nothing on refusal:
1. Bad arguments refuse `HIGH_DEBUG_SCREEN_RESCAN_INVALID`.
2. A pending, held (§3.3) or `RESCAN_CLEARED` `DEVELOPER_OUTPUT` record must exist, else `HIGH_DEBUG_SCREEN_NO_REFUSAL` or `_RESCAN_NOT_APPLICABLE`. A legacy record refuses `_RECOVERY_LEGACY`. An overflow record needs its full batch history (`:697-756`) so every hit rule is known, else `_HITS_OMITTED`.
3. A stored release is returned unchanged (§3.4). A stored HIT under the same screen digest refuses `_RESCAN_REPEATED`.
4. **Fix installed and verified:** `--fix-sha` must be on authenticated `main` and in a non-stale installed source (reusing `LLMTOOL_SOURCE_STALE` is UNVERIFIED), else `_RESCAN_FIX_UNVERIFIED`. The installed `screen_sha256` must differ from the recorded one, else `_RESCAN_SCREEN_UNCHANGED`.
5. **Rule family:** if the ledger (§3.5) holds another fix SHA for any hit rule of the record, or the record has `route2_parent`, refuse `_RESCAN_OWNER_REQUIRED`.
6. **Wrong D6:** `--d6`, the current branch and the head must equal `d6`, `d6_branch` and `d6_head`, else `_RECOVERY_BINDING`.
7. **Source:** rebuild in memory from the D6 against `base_sha`; absent refuses `_SOURCE_MISSING`; a different digest, unreadable or oversized source refuses `_SOURCE_MISMATCH`.
8. **Ledger, then rescan:** write the ledger entries (§3.5), then rerun the installed `developer_diff_sensitive` with `sources` per `screen_mode`, read as `_revision_sources` does (`task_submission_cli.py:3153-3159`).
   - True: append a HIT event (count, digest, at most 32 `rule:line`) and refuse `_RESCAN_HIT`.
   - False: create the release event, then store disposition `RESCAN_CLEARED`.

The text lives only in memory in steps 7-8: never written, logged, printed, put in an exception or sent. Output is codes and metadata (as `high_debug.py:1642-1648`).

**Events** are create-once history files (`high_debug.py:397-415`): `<refusal_id>-rescan-<screen digest>.json` and `<refusal_id>-release.json`, holding IDs, the D6 binding, digests (source, hits, both screens), fix SHA, prior disposition, result, hits, ref and time. `_read_screen_record` accepts `RESCAN_CLEARED`, overflow included, only with a matching release event (as `:564-581`).

### 3.3 Gate tightening (G3)

On `DEVELOPER_OUTPUT` records, a disposition is **held** and keeps the block until `RESCAN_CLEARED` if it is:
- `FALSE_POSITIVE`; or
- `BATCH_CONFIRMED` where any batch event is `FALSE_POSITIVE` (read through `_confirmed_batches`, `:575`).

Other classifications, and other record sources, are unchanged (Q1). This only adds a block; no screen or gate is weakened.

### 3.4 One new compliant Task

The release opens nothing itself. The refused output is never applied, copied or passed on; the refused D6 stays for audit (Q8).

`llmtool task delegate <new> --route2-release <refused T>` refuses:
- `DELEGATE_ROUTE2_RELEASE_REQUIRED` with no matching release;
- `DELEGATE_ROUTE2_SOURCE_STALE` unless the new Task's source is the authenticated `main` head;
- `DELEGATE_ROUTE2_TASK_USED` if `<refusal_id>-claim.json` exists. It is create-once and names the new Task.

The new Task's refusal record stores `route2_parent`, so step 5 stops a third Task.

**Idempotence.** A repeat after a release prints it and writes nothing. A crash before the disposition is finished on the next call (as `high_debug.py:855-857`). Concurrent calls race on one create-once link; the loser reads the winner. A same-screen HIT repeat refuses before rereading. Ledger entries are create-once, so a same-fix repeat adds nothing.

**Invariant.** Each `refusal_id` has at most one release and one claim, the claim after the release. A release exists only if its event binds the record's source digest, D6 name, branch and head and a changed screen digest, and the installed gate decided not sensitive. Every fix SHA that reached step 8 for a rule is in the ledger, whatever the result; no rule with two fix SHAs is released without a relayed Owner decision. No file holds refused text.

### 3.5 Rule-family ledger

`high-debug-screen-route2/rule-<rule>/<fix SHA>.json` is created once per hit rule and fix SHA at step 8, **before** the rescan result, so a HIT, a later release or a crash all leave the fix on record. It holds the rule, fix SHA, `refusal_id`, Task ID and time. After writing, the command relists the rule; another fix SHA there (a concurrent call) refuses `_RESCAN_OWNER_REQUIRED` before the screen runs.

### 3.6 Manual

Manual: the Route 1 fix, review and CI; classification and the metadata-only `--ref` comment; installing; judging the new Task is the same item; the Owner question; Route 1 fixes never rescanned (the ledger cannot see them; the PM checks Route 1 history); the efficiency log. Text-route refusals (G6) and legacy records stay blocked, 待後續.

## 4. Implementation plan (待實作（WP-4）)

At most 4 files per PR; test files are new, except PR 4 flips PR 1 pins. Under #763 A (`skills/ai-team-delivery/SKILL.md:65`), tests inject `developer_diff_sensitive`, the hit locator and the D6 reader. Fixtures are neutral sentinel lines the injected screen flags: no credential-shaped strings, no synthetic-data exception. The PM runs the tests.

1. **Baseline** (test only, `gw-agent`): `tools/llmtool/tests/test_developer_screen_route2_baseline.py` pins today: recovery with an injected no-hit screen refuses `HITS_MISMATCH`; `FALSE_POSITIVE`, and all-`FALSE_POSITIVE` overflow batches ending `BATCH_CONFIRMED`, clear the block (both flipped in PR 4); no sentinel in the record directory.
2. **Record binding** (3 files): `high_debug.py`, `task_submission_cli.py`, `tools/llmtool/tests/test_developer_screen_record_binding.py`. Tests: each caller's mode; old records load; a bad digest, head or mode refuses `HIGH_DEBUG_SCREEN_RECORD_INVALID`.
3. **screen-rescan and ledger** (3 files): `high_debug.py`, `task_submission_cli.py`, `tools/llmtool/tests/test_high_debug_screen_rescan.py`.
   - New codes: `HIGH_DEBUG_SCREEN_RESCAN_INVALID`, `_NOT_APPLICABLE`, `_FIX_UNVERIFIED`, `_SCREEN_UNCHANGED`, `_HIT`, `_REPEATED`, `_OWNER_REQUIRED`.
   - Reused codes: `_SOURCE_MISSING`, `_SOURCE_MISMATCH`, `_RECOVERY_BINDING`, `_RECOVERY_LEGACY`, `_NO_REFUSAL`, `_HITS_OMITTED`.
   - Tests: **missing source**; **digest mismatch**; **hit** (event and ledger written, block kept); **wrong D6** (name, branch, head); unchanged screen; unverified fix; legacy record (any new key missing) and overflow without full batches refuse with no reader call; **fix A hits, then fix B refuses `_OWNER_REQUIRED`**; a racing second fix SHA refuses; clear (release, `RESCAN_CLEARED`, recorded mode used).
4. **Gate and claim** (4 files): `task_submission_cli.py`, `high_debug.py`, `tools/llmtool/tests/test_delegate_route2_release.py`, the PR 1 baseline file. Codes: `DELEGATE_ROUTE2_RELEASE_REQUIRED`, `_SOURCE_STALE`, `_TASK_USED`. Tests: `FALSE_POSITIVE` alone keeps the block; **overflow:** all-`FALSE_POSITIVE` or mixed batches ending `BATCH_CONFIRMED` keep it until `RESCAN_CLEARED`, other-class batches clear as today; one claim passes, a second refuses; a stale source refuses; a child refusal blocks a rescan.
5. **Repeat and no-text** (test only, `gw-agent`): `tools/llmtool/tests/test_high_debug_screen_rescan_repeat.py`.
   - **Repeated call:** same release, history and ledger unchanged; a same-screen HIT refuses `_REPEATED` with no reader call; an interrupted finish clears next call; a race leaves one release.
   - **No text stored:** after every path, no sentinel in the store, screen, history, ledger or capture directories, stdout or stderr.
6. **Docs** (2 files): `skills/ai-team-delivery/pm-continuation.md` (the `[NOT LIVE]` line `:70`) and `CLAUDE.md` (status marks).

## 5. Open questions (recommended defaults)

- **Q1. G3 tightening:** yes, for `DEVELOPER_OUTPUT` `FALSE_POSITIVE` and `BATCH_CONFIRMED` with any `FALSE_POSITIVE` batch. It changes today's flow, so the PM confirms.
- **Q2. Screen digest:** installed `developer_diff_screen.py` and `test_output_excerpts.py` (`developer_diff_screen.py:50`); other inputs UNVERIFIED.
- **Q3. Legacy records:** refuse `_RECOVERY_LEGACY`; the item stays blocked. Missing bindings may later come only from existing evidence proven trustworthy (whether any exists is UNVERIFIED), 待後續.
- **Q4. "No hit":** the gate decision is False. The hit locator cannot clear a Python-release fix.
- **Q5. Text-route refusals (G6):** no D6 holds the message, so they stay blocked (`_SOURCE_MISSING`), 待後續.
- **Q6. Redacted capture** (`task_submission_cli.py:3356-3399`): never a rescan source; unchanged here, 待後續.
- **Q7. Owner trigger:** a family is a screen rule name. A second fix SHA refuses until the PM passes a relayed Owner decision URL (`--owner-ref`), stored in the ledger. The tool never decides.
- **Q8. Reuse of the refused output:** no. Only one new Task from latest `main`.
- **Q9. AGY base (G5):** record the snapshot head as `base_sha`. Records from before PR 2 are legacy and stay blocked (fails closed).
