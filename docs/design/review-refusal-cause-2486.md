# #2486: why a formal review is refused OUTPUT_NOT_JSONL with a one-line reply

Status: read-only investigation note. No code was changed and nothing was run.
Every statement marked **Source** comes from reading the files listed under
"Files read". Anything not decidable from those files is marked **UNKNOWN**.

## Question

Formal reviews through llmtool are refused `OUTPUT_NOT_JSONL`. The refusal
receipt shows `reply_diagnostic` with `kind=text`, `lines=1` and `cause=other`,
and the reply is about 190-250 bytes. No Codex session file is found. The same
account and model work through a direct `codex exec`. Which code paths in the
formal review dispatch can produce such a refusal, which of them match the
evidence, and what is the most likely cause?

## Evidence as given in the Task

- Issue #2486 comments c7068744797, c7068531229, c7066903506. They were not
  available to this investigation; only the Task text was.
- Refusal receipts: PR #2495 (task 93f385d0, head 28be5945, version 2) and
  PR #2508 (task f6e1b8bd, head 4c99af82, version 1). Both show
  `reply_diagnostic` with `cause=other`, `kind=text`, `lines=1`.
- Successful formal reviews in the same hours on PRs #2490, #2498, #2500, #2496.

Not given, so UNKNOWN: the receipt's `excerpt` value, the `first` value, the
exact `bytes` per receipt, whether the version 2 receipt of #2495 is an
automatic re-run, the claim's `account_mode` and `account`, and which Reviewer
route (Codex or Claude) the four successful reviews used.

## Files read

- `tools/llmtool/llmtool/review_dispatch.py`
- `tools/llmtool/llmtool/review_result.py`
- `tools/llmtool/llmtool/task_advance.py`
- `tools/llmtool/llmtool/providers/codex_cli.py`
- `tools/llmtool/llmtool/providers/cli_lifecycle.py`
- `tools/llmtool/tests/test_review_refusal_rerun_2486.py`
- `tools/llmtool/tests/test_review_reply_excerpt_2486.py`

## What the source fixes before any candidate

These facts hold for every candidate below.

1. **Exit status was 0.** `review_dispatch._dispatch_codex` runs `codex` with
   `subprocess.run`, the prompt on stdin, stdout and stderr both piped. A
   non-zero exit returns `REVIEW_NOT_POSTED` with the message "Codex Reviewer
   exited unsuccessfully; details omitted" and no failure reason, so no
   `OUTPUT_NOT_JSONL` claim reason and no refusal receipt. A timeout and a
   missing or unstartable runtime refuse with their own codes
   (`REVIEWER_TIMEOUT`, `REVIEWER_RUNTIME_UNAVAILABLE`). So an
   `OUTPUT_NOT_JSONL` receipt can only come from an exit status of 0.
2. **The reply is on stdout.** Stderr is piped and never read or stored. A
   message the CLI wrote only to stderr cannot appear in any receipt.
3. **Where `OUTPUT_NOT_JSONL` is raised.**
   `review_dispatch._parse_codex_review_inner` raises it when a non-empty line
   of stdout, split only at newline bytes, fails `json.loads` with the
   duplicate-key-rejecting hook, or raises a Unicode or recursion error. A
   stream with no `thread.started` and no agent message is therefore refused
   at the first bad line, before `NO_THREAD_ID` or `NO_FINAL_MESSAGE` can be
   reached.
4. **Two receipt branches.** `review_dispatch._parse_codex_review` first calls
   `_codex_final_message`.
   - **Branch A** (no usable final agent message): the diagnostic is computed
     over the whole stdout by `_reply_diagnostic_for_raw`, and the receipt
     `excerpt` is the `OUTPUT_SHAPE ...` word string from `_jsonl_shape`.
   - **Branch B** (a readable agent message came before the bad line): the
     diagnostic is computed over that final agent message by
     `_reply_diagnostic`, and the receipt `excerpt` is a `REPLY_SHAPE ...`
     word string.
   - In both branches the receipt carries a `reply_diagnostic` only if the
     sensitive-content screen passed. A screen refusal gives the label
     `REVIEW_OUTPUT_WITHHELD` and no diagnostic. Undecodable bytes give
     `REVIEW_OUTPUT_UNDECODABLE`, empty output gives `REVIEW_OUTPUT_ABSENT`,
     and neither has a diagnostic. Since both receipts have a diagnostic, the
     screen passed and the bytes decoded.
5. **What `lines=1` means.** `review_dispatch._reply_diagnostic` computes
   `lines` as the count of newline characters in the unstripped text plus
   one. So `lines=1` means the analysed text has no newline at all, not even a
   trailing one. In branch A that is the whole of stdout.
6. **What `kind=text` means.** The text did not parse with the strict JSON
   reader and does not start with a code fence. A one-line JSON object with a
   duplicated key also gives `kind=text`, because the same duplicate-key hook
   is used.
7. **What `cause=other` means.** `review_dispatch._reply_cause` returns `other`
   when none of four closed word lists matched (case-insensitive, whole
   words): usage or rate limit (`rate limit`, `usage limit`, `quota`,
   `too many requests`, `429`), context or input size (`context length`,
   `context window`, `input too long`, `prompt too long`, `request too large`
   and close forms), authentication (`unauthorized`, `authentication`,
   `not logged in`, `log in`, `login`, `401`, `403`, `invalid api key`,
   `credential`) and model unavailable (`model unavailable`, `no such model`,
   `overloaded`, `service unavailable`, `502`, `503`, `capacity`). So
   `other` rules out those exact words. It does not rule out the same meaning
   in other words, for example "limit" alone, "credits", "plan", "upgrade",
   "exceeded", "sign in", "expired" or "try again later".
8. **`--ephemeral` is in the argv.** `review_dispatch._codex_argv` passes
   `--ephemeral`. The Codex thread id used for the claim comes from the
   `thread.started` event in stdout, never from a session file. By the flag's
   name, a successful review would also leave no session file, so "no session
   file" probably does not separate a refused run from a successful one.
   What the flag does inside Codex is UNKNOWN from the source.
9. **No fallback and one re-run.** `task_advance._reviewer_fallback` only
   switches Reviewer for `ROLE_MODEL_NOT_ALLOWED`,
   `REVIEWER_ACCOUNT_UNCONFIGURED` and `REVIEWER_NOT_ALLOWED`. An
   `OUTPUT_NOT_JSONL` refusal gets no Claude fallback. Instead
   `task_advance._review_refusal_rerun` allows exactly one automatic re-run
   of the same head and contract through `review_dispatch.dispatch_review`
   with `refusal_rerun=True`. The re-run's receipt carries
   `automatic_rerun`; the first receipt gets a `rerun` key.
   `review_dispatch.mark_refusal_rerun` writes it before the re-run starts.

## Candidate paths

Each entry gives where the reply originates, the text shape it would produce,
and whether the evidence confirms, contradicts or cannot decide it.

### C1. The Codex process prints one unterminated plain line to stdout and exits 0

- **Where:** the external `codex` process started by
  `review_dispatch._dispatch_codex`. Source cannot say what it prints or why.
- **Shape:** branch A, `OUTPUT_SHAPE` excerpt for one bad line of one,
  `kind=text`, `lines=1`, `cause` from the four word lists, `first` probably
  `letter`.
- **Match to evidence:** consistent with every receipt field given: exit 0,
  one line, no newline, screen passed, `cause=other`. It needs no other
  assumption about stdout.
- **Verdict:** **cannot decide.** The receipts are compatible with it, but
  they do not show the text, so nothing in them proves the process, not the
  parser, produced the line. The wording must avoid the listed cause words.

### C2. Environment or account differs between the llmtool child and the direct run

- **Where:** `review_dispatch._dispatch_codex` builds the child environment
  from `PATH` (the executable's directory plus `/usr/bin` and `/bin`),
  `HOME`, `LC_ALL=C`, `NO_COLOR=1`, and `CODEX_HOME` only when the role's
  `config_dir` is set. It then passes the result through
  `github_identity.ai_github_env`, which is outside the files read, so what it
  adds or removes is UNKNOWN. The child does not inherit the caller's other
  variables, for example proxy or certificate settings, or any other `CODEX_*`
  setting. The argv also adds `--ignore-user-config`, `--ignore-rules`,
  `--strict-config`, `-s read-only` and `--no-daemon`.
- **Shape:** one-line CLI notice, as in C1.
- **Match to evidence:** "works through a direct `codex exec`" does not show
  the same environment or flags, because the direct command's environment is
  not stated. A static difference would, however, fail every Codex review,
  and the Task reports successful reviews in the same hours. That contradicts
  C2 only if those reviews used the Codex route; their route is UNKNOWN.
- **Verdict:** **cannot decide**, and contradicted as a sole cause if the
  successes were Codex runs with the same `config_dir`.

### C3. The input is PR-dependent: a stdin prompt the CLI answers with a one-line notice

- **Where:** stdin is the rules prefix from `review_dispatch._review_rules`
  plus the rendered review prompt from `task_submission_cli`, whose source is
  outside the files read. PR-dependent content or size is possible; the prompt
  size is not stored in the claim or the receipt.
- **Shape:** one-line CLI notice, as in C1. The input-size word list would
  catch common size wording, but not every wording.
- **Match to evidence:** the pattern "PRs #2495 and #2508 refused, PRs #2490,
  #2498, #2500, #2496 succeeded" is what an input-dependent cause predicts and
  a static one does not. It is also what a time-dependent cause predicts.
- **Verdict:** **cannot decide.** Nothing recorded separates input from time.

### C4. A readable stream with an agent message, followed by one bad line (branch B)

- **Where:** the stream itself. `review_dispatch._parse_codex_review` then
  reports the diagnostic of the last agent message, not of stdout. The test
  `test_jsonl_stream_with_bad_line_keeps_content_free_diagnostic` names this
  "the PR #2484 form".
- **Shape:** a `REPLY_SHAPE` excerpt. `kind=text`, `lines=1` and `cause=other`
  would describe a one-line plain agent message. A model run happened in this
  path, because an agent message exists.
- **Match to evidence:** receipts compatible only if the `excerpt` begins with
  `REPLY_SHAPE`. It also needs a non-JSON line inside an otherwise valid
  stream. The test name shows this form occurred before (PR #2484), but the
  source does not say why Codex emitted such a line. A clean stream with a
  plain final message would instead end as `VERDICT_NOT_JSON`, not
  `OUTPUT_NOT_JSONL`.
- **Verdict:** **cannot decide** from the Task text; the receipt `excerpt`
  settles it (see the check below). It is the one candidate in which a model
  did run, so the premise "no model run" would be wrong if it holds.

### C5. A genuine one-line JSON event rejected by the strict reader

- **Where:** `review_dispatch._unique_json_object` rejects a duplicated key,
  and deep nesting raises a recursion error. Both become `OUTPUT_NOT_JSONL`,
  and `_reply_diagnostic` then reports `kind=text` because it uses the same
  reader.
- **Shape:** `first=brace`. It needs stdout to be exactly one event with no
  newline.
- **Match to evidence:** `first` is not given. A single unterminated event is
  not what an event stream looks like, and the source shows no Codex event
  with duplicate keys.
- **Verdict:** **cannot decide**, unlikely.

### C6. A shim or different `codex` executable

- **Where:** `review_dispatch._dispatch_codex` resolves `codex` with
  `shutil.which` from the llmtool process's own `PATH`. If that differs from
  the shell used for the direct run, a different program could answer.
- **Shape:** one-line text, exit 0 only if the shim exits 0.
- **Match to evidence:** the same lookup serves the successful reviews, so a
  fixed wrong executable would fail them too (if they used Codex).
- **Verdict:** **cannot decide**, contradicted as a sole cause under the same
  condition as C2.

### Paths that the evidence excludes

- **Non-zero exit, timeout, missing runtime:** different codes and no
  `OUTPUT_NOT_JSONL` receipt (fact 1).
- **Stdout over the size ceilings or an oversized final message:**
  `OUTPUT_UNREADABLE`, not `OUTPUT_NOT_JSONL`.
- **Non-UTF-8 stdout:** may raise `OUTPUT_NOT_JSONL`, but the receipt then
  carries `REVIEW_OUTPUT_UNDECODABLE` and no diagnostic. Contradicted by
  `kind=text`.
- **Screen refusal:** the label is `REVIEW_OUTPUT_WITHHELD` and there is no
  diagnostic. Contradicted by the diagnostic.
- **Empty stdout:** label `REVIEW_OUTPUT_ABSENT`. Contradicted.
- **`CodexCLIProvider` in `providers/codex_cli.py` and
  `providers/cli_lifecycle.py`:** `review_dispatch` does not call either.
  The provider runs `codex exec` without `--json` and accepts any non-empty
  text as a reply, so it would never refuse as `OUTPUT_NOT_JSONL`. The
  lifecycle module owns signals and process teardown and writes no output.
  Neither is on the formal review path.
- **The injected `codex_runner` argument of `dispatch_review`:** used by tests
  only; production uses `_dispatch_codex`.
- **The Claude Reviewer route:** `review_dispatch._dispatch` returns a session
  id from the bridge and has no stdout parser, so it cannot yield this
  refusal.
- **`task_advance` itself:** it only re-runs the same dispatch (fact 9) and
  produces no reply text. If #2495 version 2 is an automatic re-run (UNKNOWN),
  its repeat means the same prompt gave the same refusal again, which favours
  C3 or a persistent cause over a one-off.

## Most likely cause

**C1: the Codex process wrote one unterminated plain-text line to stdout and
exited 0, instead of a JSONL event stream.** This is an inference, not a
source fact.

Reasons:

- It is the only candidate that needs no assumption beyond the receipt fields:
  exit 0, no newline in the whole stdout, a decodable text that the screen
  passed.
- C4 needs an extra condition (a stray non-JSON line inside a valid stream)
  and a one-line plain model message, and the receipt `excerpt` is not
  known to fit it. C5 needs a one-event,
  unterminated stream. C2, C3 and C6 are not rivals so much as possible
  reasons why the process prints the line.

What the source cannot give: the text, why Codex printed it, and whether the
trigger is account, environment, input or time (C2, C3). The word lists leave
most limit, plan and sign-in wordings in `other`, so `cause=other` does not
show the line is unrelated to a limit or login problem.

## Smallest observable check

1. **No new code, read the two receipts first.** Both are small private files
   with fixed words only.
   - `excerpt` begins `OUTPUT_SHAPE` means branch A (C1, C2, C3, C5).
     It begins `REPLY_SHAPE` means branch C4, and the Codex model did run.
   - `first` is `brace` points to C5. `letter` fits C1.
   - Presence of `automatic_rerun` on the version 2 receipt of #2495 shows
     whether the same head failed twice.
   - Compare the failing claims' `account_mode` and `account` with those of
     the successful reviews, to test C2.
2. **Confirm C1 with one replay and read the line.** For the refused head,
   run the exact command `review_dispatch._codex_argv` builds (same model and
   effort, `-s read-only`, `--ephemeral`, `--json`, the Reviewer D6 as `--cd`)
   with the same environment the dispatch builds and `CODEX_HOME` set as the
   role configuration sets it, with the same prompt on stdin. Look only at the
   exit status, the number of newline bytes in stdout and the printed line on
   the terminal. If it prints the same one line with exit 0, C1 is confirmed
   and the text names the sub-cause. If it succeeds, repeat once with the
   caller's full shell environment (to test C2), then with a smaller prompt
   (to test C3).
3. **Optional, read-only side check.** The file the dispatch writes under the
   user's `.local/state/llmtool/codex-quota.json` (`_record_codex_quota`)
   keeps the newest `used_percent`, `window_minutes` and `resets_at` seen in
   any JSONL stream. Compare its `read_time` and values with the two refusal
   times. Whether Codex emits those figures is UNKNOWN, so an absent or stale
   file decides nothing.

Follow-up (not done here, outside this Task's write scope): the receipt could
also store the child's exit status and the stdout and stderr byte counts, as
fixed numbers with no text, so the next refusal answers step 2 without a
replay.

## Not decidable from the source (UNKNOWN)

- What Codex prints on stdout, instead of JSONL events, before or without a
  model run, and when it does so with exit 0.
- What `--ephemeral` and `--strict-config` do in Codex.
- What `github_identity.ai_github_env` and the rendered review prompt add to
  the child environment and stdin.
- The Reviewer route of the four successful reviews.
