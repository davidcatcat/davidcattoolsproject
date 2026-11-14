# WP-5 design: no-PR read-only `task high-debug` entry (#1943)

**Proposal only; not a review or acceptance.** Source: `main` at `a2b6f4d891d89f16f3411122e25f307a54538a01`. Scope: Owner #1928 item 4, #1943 WP-5 and Owner O6. Code paths are under `tools/llmtool/llmtool/`. Claims about today cite `path:line` at that SHA. Anything not read is UNVERIFIED. The entry itself is 待實作（WP-5）.

## 1. Today (現行)

- **CLI.** Required: `task_id`, `--executor-task`, `--pr` (int), `--family`, plus one of `--failed-corrections` or `--early` (`task_submission_cli.py:1021-1030`). These are passed to `run_high_debug_with_fallback` (`task_submission_cli.py:2417-2423`). A fresh source is needed (`cli.py:63`, `:594-599`). Refusals print `high-debug refused: CODE (detail)` (`cli.py:736-746`).
- **No PR or no executor Task.** argparse rejects the call before any refusal code (`task_submission_cli.py:1023-1024`). How the usage error is shown is UNVERIFIED. A bad `--pr` value refuses `HIGH_DEBUG_ARGUMENT_INVALID` (`high_debug.py:1275-1281`).
- **No committed D6 head.** The Task's completion evidence must name the executor Task and `common_dir`, else `HIGH_DEBUG_D6_UNAVAILABLE` (`high_debug.py:1364-1375`). The registration `result_sha` must be 40-hex and equal the D6 HEAD, else `HIGH_DEBUG_HEAD_MISMATCH` (`:1376-1377`). So a Task with no completion and no commit cannot get a Debug today.
- **Admission, in order.** Trigger check: at least 2 failed corrections backed by repair claims, or an early reason (`high_debug.py:1282-1295`). Screen block (`:1296`). Family matches the PR repair claims (`:1299-1305`). Evidence file (`:1306-1334`). Dispatch key (`:1336-1339`). Claude account (`:1342-1358`). Model (`:1363`). D6 and head (`:1364-1377`). No open dispute (`:1380-1382`).
- **Caps.** At most 8 entries per Task, else `HIGH_DEBUG_LIMIT` (`high_debug.py:1383-1384`). One AI Debug per lineage (`:1247-1264`).
- **Run.** Rule cards come from authenticated `main` (`high_debug.py:1105-1130`, `:1389`). A new Debug D6 is created at the head (`:1392-1397`, `:1429-1439`). Codex runs read-only (`:998-1011`); Claude gets `Read,Glob,Grep` only (`:1014-1028`). A dirty tree refuses `HIGH_DEBUG_WROTE_FILES` (`:1504-1508`).
- **Screen.** Evidence is capped at 8192 bytes. A sensitive hit writes a text-free record and refuses `HIGH_DEBUG_EVIDENCE_SENSITIVE` (`high_debug.py:1306-1333`). The report is checked for size (16384) and sensitive content, else `HIGH_DEBUG_REPORT_INVALID` (`:1520-1538`). Records clear only by disposition or batch recovery (`:640-671`, `:866-908`). A pending record blocks the Task under any High Debug family (`:1205-1239`).
- **Report flow.** Status is read from Outcome/cost (`high_debug.py:1184-1202`, `:1539-1540`). Then: a Task `high_debug` entry (`:1541-1546`), a PR comment (`:1547`), a `HIGH_DEBUG` completion event (`:1549-1552`), and metadata JSON on stdout (`:1553-1555`).
- **Routes.** Codex first; Claude only on quota or auth failure, else `HIGH_DEBUG_WAITING` (`high_debug.py:942`, `:1563-1587`).
- **Repair link.** Repair #2 accepts only an `RCA_CONFIRMED` entry in the Task's `high_debug` list, bound to its completion event (`task_submission_cli.py:7115-7203`, `:7346-7395`).
- **Drift (not fixed here).** `CLAUDE.md:85` still marks the `codex-debug`/`claude-debug` keys 待實作（WP-B）, but `high_debug.py:1336` uses them.

## 2. Requirements

The three Owner comments are outside this Task's read paths, so their exact wording is UNVERIFIED. Below is the Task contract's wording and the Debug card's restatement, not a verbatim quote (Q1).

- **#1928 c6877449289 item 4:** a controlled read-only entry when there is no PR and no committed D6 head.
- **#1943 c6879556133:** read-only, screened, writes nothing, no review claim.
- **O6, #1943 c6879415056** (as `skills/role-cards/debug.md:31` restates it): "`claude-debug` design consultation (O6): at most once per work package and, at first, at most 10 per week. Only for complex state machines, claim chains, important security or data-integrity design, or an evidenced major technical disagreement. Ordinary questions start no second model. No new dispatch key; it never replaces formal cross-family review; the PM decides; disputes go to the arbiter."
- **Card rules** (`skills/role-cards/debug.md:27-30`): output is `type=debug_report` or `type=design_advice`. It has no `Verdict:` line, no `review_verdict` marker and no review claim. It changes no repair count. Its run is never the lineage's formal Reviewer. RCA keeps `codex-debug` first.

## 3. Design (待實作（WP-5）)

### 3.1 CLI

```text
llmtool task high-debug <task_id> --no-pr --kind debug_report|design_advice
    --wp <id> --question-file <path> [--evidence-file <path>]
    --source-sha <40-hex> [--executor-task <D6> --diff-sha256 <64-hex>]
    [--case <case>] [--disagreement-ref <URL>] [--route codex|claude]
```

- `--no-pr` excludes `--pr`, `--failed-corrections` and `--early`. Their argparse `required` flags move into the run function (`task_submission_cli.py:1024-1028`).
- **Pin, required.** `--source-sha` must be reachable from authenticated `main`. With `--executor-task`, the D6 must be registered and owned by this Task, and its base must equal `--source-sha`. Its uncommitted diff must hash to `--diff-sha256`.
- `--case` is required for advice. Values: `state_machine`, `claim_chain`, `security_design`, `data_integrity_design`, `disagreement`. `disagreement` needs `--disagreement-ref`.

### 3.2 Evidence bound

The run binds Task, WP, kind, case, route, source SHA, D6 and diff digest (or `none`), and the question, evidence, rules (`high_debug.py:1130`) and report digests. The diff is screened and sent as untrusted evidence. It is never applied to a tree. A scratch Debug D6 is created at the source SHA, as today (`high_debug.py:911-922`).

### 3.3 Output

- stdout prints one JSON object: `type` (`debug_report` or `design_advice`), `advisory=true`, the bindings and the screened report.
- `debug_report` keeps `REPORT_SECTIONS` (`high_debug.py:22-24`). `design_advice` uses Identity and source, Question, Options, Recommendation, Risks and Outcome/cost. Its outcome word is `ADVICE_GIVEN`, `ADVICE_UNSURE` or `BLOCKED`.
- The tool prepends a fixed line: "Advisory only; not a review or acceptance criterion."
- A report line starting `Verdict:`, or a `review_verdict` or `<!-- activity:` marker, refuses `HIGH_DEBUG_CONSULT_REVIEW_CLAIM`. It is never reworded.

### 3.4 Never

- No `high_debug` entry and no completion event, so repair #2 can never use it (`task_submission_cli.py:7198-7202`).
- No GitHub post and no unposted record (`high_debug.py:158-207`).
- No change to `_admit_debug`, the 8-entry cap or any repair count. The PR-only family and dispute checks are skipped.
- No new dispatch key: it reuses `codex-debug`/`claude-debug` (`high_debug.py:1336-1339`).
- A write attempt refuses `HIGH_DEBUG_WROTE_FILES` (`high_debug.py:1504-1508`).

### 3.5 Screen

- `_require_screen_cleared` runs first, unchanged. The question, evidence and diff are each screened. The report gets today's checks (`high_debug.py:1520-1538`).
- The family is `consult-<wp>` for advice and `--family` for RCA. Records bind `d6` to the scratch D6, so recovery has a binding (`:814-815`).
- The diff gets a new source, `D6_DIFF` (`HIGH_DEBUG_DIFF_SENSITIVE`), rebuilt from the D6 for recovery like `DEVELOPER_OUTPUT` (`:58-60`, `:774-783`).
- #981 disposition rules apply unchanged: metadata only; no retry, rewording or hint.

### 3.6 Routes

- `debug_report`: `codex-debug` first; `claude-debug` only on `DEBUG_FALLBACK_CODES` (`high_debug.py:1563-1587`).
- `design_advice`: the PM names the family that differs from the planned formal Reviewer with `--route`. No automatic fallback, since that would start a second model. A same-family pair is recorded.

### 3.7 O6 counts

- **Ledger.** A private, metadata-only ledger sits beside `high-debug-screen` (`high_debug.py:359-360`). It holds `high-debug-consult/wp-<wp>.json` and `week-<ISO week>-<n>.json`, with n = 1..10.
- **Reservation.** Slots are created after every local check passes and just before launch, with the atomic create-once link (`high_debug.py:397-415`).
- **Refusals.** An existing WP slot refuses `HIGH_DEBUG_CONSULT_WP_USED`. With no free week slot, the call refuses `HIGH_DEBUG_CONSULT_WEEK_CAP`. A bad ledger refuses `HIGH_DEBUG_CONSULT_LEDGER_INVALID`.
- **Updates.** A slot moves from `RESERVED` to `DONE` or `FAILED` with the report digest (`:418-433`). Slots are never deleted.

### 3.8 Idempotence and concurrency

- **Invariant.** Each WP ever has at most one advice slot, and each ISO week at most 10. Each slot exists before its launch and is never removed, so counts never fall and a crash fails closed.
- **Two calls at once.** Only one link-create of `wp-<wp>.json` succeeds; the other refuses `HIGH_DEBUG_CONSULT_WP_USED` before launch. Week slots take the lowest free n. A lost race tries the next n.
- **Repeated call.** It refuses, with the existing slot state and report digest as detail. It never relaunches.
- **D6 changed after pinning.** The call refuses `HIGH_DEBUG_CONSULT_DIFF_MISMATCH` before reservation.

## 4. Implementation plan (待實作（WP-5）)

Each PR touches at most 4 files. A code written `_X` below keeps the prefix of the code before it. The test directory path is UNVERIFIED. Tests follow the #763 A rule: screen refusals inject the screen function, and only detector tests use labelled synthetic data.

1. **Ledger.** New `high_debug_consult.py` and one test file. Codes: `HIGH_DEBUG_CONSULT_WP_USED`, `_WEEK_CAP`, `_LEDGER_INVALID`. Tests: first slot passes; same WP again refuses; an 11th in a week refuses; the next week passes; a two-process race leaves exactly one slot; a corrupt or symlinked ledger refuses.
2. **Base-SHA entry.** `task_submission_cli.py`, `high_debug_consult.py`, `high_debug.py` (shared helpers) and one test file. Codes: `HIGH_DEBUG_CONSULT_ARGUMENT_INVALID`, `_PIN_REQUIRED`, `_PIN_INVALID`, `_CASE_INVALID`, `_REVIEW_CLAIM`. Tests:
   - both kinds pass with an injected runner;
   - refusals: missing pin; pin not on `main`; `--no-pr` with `--pr`; bad case; runner write (`HIGH_DEBUG_WROTE_FILES`); a `Verdict:` line;
   - nothing reaches `high_debug`, completion events or GitHub (the post function is injected and must stay uncalled);
   - the repair #2 gate still refuses;
   - RCA fallback works; advice has no fallback.
3. **Diff pin.** `high_debug_consult.py`, `high_debug.py` (`D6_DIFF`) and two test files. Codes: `HIGH_DEBUG_CONSULT_D6_MISMATCH`, `_DIFF_MISMATCH`, `HIGH_DEBUG_DIFF_SENSITIVE`. Tests: a valid pin passes; wrong or foreign D6 refuses; base differs from the pin; diff changed; an injected screen hit writes a record and blocks the next call; recovery binds to the D6.
4. **Docs.** `skills/role-cards/debug.md`, `skills/ai-team-delivery/high-debug-report.md` (advice format) and `CLAUDE.md` (status marks; the `:85` drift).

## 5. Open questions for the PM

- **Q1. Quotes.** Section 2 is not verbatim. Default: the PM checks it against the three comments before review.
- **Q2. What "writes nothing" allows.** The scratch D6, the O6 ledger and screen records are private tool state, not repo, Task-entry or GitHub writes. Default: allow them; without them the caps and screen cannot be enforced.
- **Q3. Report keeping.** Default: stdout only, with the digest in the slot. A lost stdout costs the slot.
- **Q4. RCA cap.** Default: one no-PR `debug_report` per Task and pin, at most 2 per Task, and no O6 week cap.
- **Q5. Codex advice under O6.** O6 names only `claude-debug`. Default: apply both caps to all advice until the Owner relaxes this (relayed by the PM).
- **Q6. Week.** Default: ISO week in UTC, which keeps slot creation atomic. A rolling window is 待後續.
- **Q7. Diff size.** Default: the 8192-byte evidence limit (`high_debug.py:28`); a larger diff refuses `HIGH_DEBUG_EVIDENCE_INVALID`.
