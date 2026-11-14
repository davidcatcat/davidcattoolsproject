# WP-4 design: digest-bound rescan of refused Developer output (#1943)

**Proposal only; not a review or acceptance.** Source: `main` at `23b1af0d5d5eddef06cc9ca861d621cc30dd05bc`. Code paths are under `tools/llmtool/llmtool/`; `path:line` is at that SHA. Anything not read is UNVERIFIED. The design is 待實作（WP-4）.

## 1. Today (現行)

**Screen.** `developer_diff_sensitive` runs the shared screen on added lines and releases only a proven plain-code Python keyword match. Errors keep the refusal (`developer_diff_screen.py:70-85`, `:36-39`). Callers in `task_submission_cli.py`:
- The strict check returns `DEVELOPER_OUTPUT_SENSITIVE` (`:3181-3182`). It is used by text normalization (`:3206`, `:3216`, `:3253`), the text route (`:7696-7702`) and the cumulative D6 diff with base-revision pre-images (`:3338-3340`, `:6967-6969`).
- OpenCode passes pre-images at the snapshot head (`:5036-5037`). AGY passes none (`:5456`).

**Record.** The record is written before the Task update (`task_submission_cli.py:3519-3522`). It holds:
- the code, the source `DEVELOPER_OUTPUT`, up to 32 `rule:line` hits, the total, `omitted` and a hit-list digest;
- the byte count and a SHA-256 of the screened output;
- `d6` (the completion's `executor_task_id`), a `refusal_id` and an append-only refusal event.

No text is kept (`:3432-3458`; `high_debug.py:471-511`). Hits are rule names and lines only (`task_submission_cli.py:3416-3429`). A separate redacted diagnostic capture goes to `rejected-developer-output/`, replaced by a marker if it still trips the screen (`:3356-3399`).

**Block.** The delegate gate checks every Task on the same executor D6 (`:7264-7270`). Any stored disposition clears it (`:3504`), including `FALSE_POSITIVE` from `screen-disposition` (`high_debug.py:640-671`). That command checks only the stored hit list (`:664-665`), with no digest, D6 or history check.

**screen-recover** (`high_debug.py:794-908`, CLI `:1590-1649`) is for overflow records only (`:811-813`). It binds `d6` by name; branch and head are not recorded (`:497`, `:814-815`). It rebuilds the output from the D6 against `developer.source_sha` (`task_submission_cli.py:3461-3478`, `high_debug.py:767-783`) and checks the digest (`:816-820`) and the refusal and batch history (`:697-756`, `:828-831`). It requires the **same** hits, else `HIGH_DEBUG_SCREEN_HITS_MISMATCH` (`:825-827`).

**Gaps against Route 2:**
- G1. screen-recover proves the refusal is unchanged; it cannot express "no hit".
- G2. It rescans with `_screen_hits`, which lacks the Python release (`high_debug.py:759-763`), not the gate decision.
- G3. `FALSE_POSITIVE` alone releases the D6 block (`task_submission_cli.py:3504`); Route 2 says no opinion releases it.
- G4. The block is keyed to the old D6 (`:7264-7270`). Whether anything stops a second new Task on a new D6 is UNVERIFIED; the one-Task limit is manual.
- G5. AGY diffs against `snapshot.head` (`:5449`); the rebuild uses `developer.source_sha` (`:3475`). Equality is UNVERIFIED, as is `task_completion.py` (not read).
- G6. Empty-diff and text-route refusals digest a message, not a D6 diff (`:6980-6982`, `:7699-7700`).

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

The record gains five optional keys; the reader accepts legacy, current and new key sets (`high_debug.py:64-67`, `:522-523`):
- `base_sha`: the diff base the gate used;
- `d6_branch`, `d6_head`: from the D6 registration and snapshot;
- `screen_sha256`: a digest of the installed screen code (Q2);
- `screen_mode`: the pre-image given to the screen (`none`, `base_revision` or `snapshot_head`).

All are metadata; no text.

### 3.2 `screen-rescan`

```text
python -m llmtool.high_debug screen-rescan --task-id <T> --family developer-output
    --d6 <executor D6> --fix-sha <Route 1 merge SHA> --ref <Issue comment URL>
```

Checks run in order; the first failure refuses and writes nothing:
1. Bad arguments refuse `HIGH_DEBUG_SCREEN_RESCAN_INVALID`.
2. There must be a pending `DEVELOPER_OUTPUT` record, else `HIGH_DEBUG_SCREEN_NO_REFUSAL` or `_RESCAN_NOT_APPLICABLE`. No `source_sha256` refuses `_RECOVERY_LEGACY`.
3. A stored release is returned unchanged (§3.4). A stored HIT under the same screen digest refuses `_RESCAN_REPEATED`.
4. **Fix installed and verified:** `--fix-sha` must be on authenticated `main` and in the installed source, and the install must not be stale (reusing `LLMTOOL_SOURCE_STALE` is UNVERIFIED). Else `_RESCAN_FIX_UNVERIFIED`. The installed `screen_sha256` must differ from the recorded one, else `_RESCAN_SCREEN_UNCHANGED`.
5. **Rule family:** a recorded hit rule already released under a different fix SHA (§3.5), or a record with `route2_parent`, refuses `_RESCAN_OWNER_REQUIRED`.
6. **Wrong D6:** `--d6`, the branch and the head must equal the record, else `_RECOVERY_BINDING`.
7. **Source:** rebuild in memory from the D6 against `base_sha` (else `developer.source_sha`). If absent, refuse `_SOURCE_MISSING`. A different digest, an unreadable source or one over the limit refuses `_SOURCE_MISMATCH`.
8. **Rescan:** rerun the installed `developer_diff_sensitive` with `sources` per `screen_mode`, read as `_revision_sources` does (`task_submission_cli.py:3153-3159`). An unrecorded mode means `none`, the strictest.
   - True: append a HIT event (count, digest, at most 32 `rule:line`) and refuse `_RESCAN_HIT`.
   - False: create the release event, then store disposition `RESCAN_CLEARED`.

The text lives only in process memory in steps 7-8. It is never written, logged, printed, put in an exception or sent anywhere. Output is codes and metadata, as at `high_debug.py:1642-1648`.

**Events** are create-once files in the record's history directory (`high_debug.py:397-415`): `<refusal_id>-rescan-<screen digest>.json` and `<refusal_id>-release.json`. They hold IDs, the D6 name, branch and head, the digests (source, hits, both screens), the fix SHA, the result, hits, ref and time. `_read_screen_record` accepts `RESCAN_CLEARED` only with a matching release event, like the batch check (`:564-581`).

### 3.3 Gate tightening (G3)

On `DEVELOPER_OUTPUT` records, `FALSE_POSITIVE` keeps the block until `RESCAN_CLEARED` (Q1). The other classifications are unchanged. This only adds a block; no screen or gate is weakened.

### 3.4 One new compliant Task

The release opens nothing by itself. The refused output is never applied, copied or passed on, and the refused D6 stays for audit (Q8).

`llmtool task delegate <new> --route2-release <refused T>` refuses:
- `DELEGATE_ROUTE2_RELEASE_REQUIRED` with no matching release;
- `DELEGATE_ROUTE2_SOURCE_STALE` unless the new Task's source is the authenticated `main` head;
- `DELEGATE_ROUTE2_TASK_USED` if `<refusal_id>-claim.json` exists. It is create-once and names the new Task.

The new Task's refusal record stores `route2_parent`, so step 5 stops a third Task.

**Idempotence.** A repeat after a release prints the stored release and writes nothing. A crash before the disposition is finished on the next call (as `high_debug.py:855-857`). Concurrent calls race on one create-once link; the loser reads the winner. A same-screen HIT repeat refuses before rereading.

**Invariant.** Each `refusal_id` has at most one release and one claim, and the claim follows the release. A release exists only if its event binds the record's source digest, D6 name, branch and head and a changed screen digest, and the installed gate decided not sensitive. No file holds refused text.

### 3.5 Rule-family ledger

`high-debug-screen-route2/rule-<rule>.json` is created once, at a rule's first release, with the fix SHA. A later release under a different fix SHA refuses `_OWNER_REQUIRED` (Q7).

### 3.6 Manual

These stay manual: the Route 1 fix, its review and CI; the classification and the metadata-only `--ref` comment; installing; judging that the new Task is the same item; the Owner question; the efficiency log. G6 refusals stay blocked, 待後續.

## 4. Implementation plan (待實作（WP-4）)

At most 4 files per PR. Test files are new, except that PR 4 flips one PR 1 pin. Under #763 A (`skills/ai-team-delivery/SKILL.md:65`), tests inject `developer_diff_sensitive`, the hit locator and the D6 reader. Fixtures are neutral sentinel lines that the injected screen flags; they contain no credential-shaped strings and use no synthetic-data exception. The PM runs the tests.

1. **Baseline** (test only, `gw-agent`): `tools/llmtool/tests/test_developer_screen_route2_baseline.py`. It pins today's state:
   - recovery with an injected no-hit screen refuses `HITS_MISMATCH`;
   - `FALSE_POSITIVE` clears the block (flipped in PR 4);
   - no sentinel is in the record directory.
2. **Record binding** (3 files): `high_debug.py`, `task_submission_cli.py`, `tools/llmtool/tests/test_developer_screen_record_binding.py`. Tests: each caller's mode; old records load; a bad digest, head or mode refuses `HIGH_DEBUG_SCREEN_RECORD_INVALID`.
3. **screen-rescan** (3 files): `high_debug.py`, `task_submission_cli.py`, `tools/llmtool/tests/test_high_debug_screen_rescan.py`.
   - New codes: `HIGH_DEBUG_SCREEN_RESCAN_INVALID`, `_NOT_APPLICABLE`, `_FIX_UNVERIFIED`, `_SCREEN_UNCHANGED`, `_HIT`, `_REPEATED`, `_OWNER_REQUIRED`.
   - Reused codes: `_SOURCE_MISSING`, `_SOURCE_MISMATCH`, `_RECOVERY_BINDING`, `_RECOVERY_LEGACY`, `_NO_REFUSAL`.
   - Tests: **missing source**; **digest mismatch**; **hit** (event written, block kept); **wrong D6** (name, branch, head); unchanged screen; unverified fix; legacy record; clear (release, `RESCAN_CLEARED`, gate called with the recorded mode).
4. **Gate and claim** (4 files): `task_submission_cli.py`, `high_debug.py`, `tools/llmtool/tests/test_delegate_route2_release.py`, the PR 1 baseline file. Codes: `DELEGATE_ROUTE2_RELEASE_REQUIRED`, `_SOURCE_STALE`, `_TASK_USED`. Tests:
   - `FALSE_POSITIVE` alone keeps the block;
   - one claim passes and a second refuses;
   - a stale source refuses;
   - a child refusal blocks a rescan.
5. **Repeat and no-text** (test only, `gw-agent`): `tools/llmtool/tests/test_high_debug_screen_rescan_repeat.py`.
   - **Repeated call:** same release, history unchanged; a same-screen HIT refuses `_REPEATED` with no reader call.
   - An interrupted finish clears on the next call; a race leaves one release.
   - **No text stored:** after every path, no sentinel appears in the store, screen, history, route2 or capture directories or in stdout or stderr.
6. **Docs** (2 files): `skills/ai-team-delivery/pm-continuation.md` (the `[NOT LIVE]` line `:70`) and `CLAUDE.md` (status marks).

## 5. Open questions (recommended defaults)

- **Q1. G3 tightening:** yes, for `DEVELOPER_OUTPUT` `FALSE_POSITIVE` only. It changes today's flow, so the PM confirms.
- **Q2. Screen digest:** installed bytes of `developer_diff_screen.py` and `test_output_excerpts.py` (`developer_diff_screen.py:50`). Other inputs are UNVERIFIED.
- **Q3. Records without the new keys:** allow a rescan with mode `none`, a name-only D6 check and the fix check. The digest binds the bytes. Stricter: refuse as legacy.
- **Q4. "No hit":** the gate decision is False. Raw `_screen_hits` cannot clear a Python-release fix.
- **Q5. G6 refusals:** stay blocked (`SOURCE_MISSING`), 待後續.
- **Q6. Redacted capture** (`task_submission_cli.py:3356-3399`): never a rescan source and unchanged here. Whether to stop writing it is 待後續.
- **Q7. Owner trigger:** a family is a screen rule name. A second fix SHA refuses until the PM passes a relayed Owner decision URL (`--owner-ref`). The tool never decides.
- **Q8. Reuse of the refused output:** no. Only one new Task from latest `main`.
- **Q9. AGY base (G5):** record the snapshot head as `base_sha`. Until PR 2, an AGY rescan may refuse `SOURCE_MISMATCH`, which fails closed.
