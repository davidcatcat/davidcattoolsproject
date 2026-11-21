# #2486: why a formal review is refused OUTPUT_NOT_JSONL with a one-line reply

Status: read-only investigation note, revised after formal review v1 of PR #2511
(findings F1, F2, F3), then fact-checked and corrected by the independent
development line (Owner 2026-11-22) against main `ed7caa45`, the stored refusal
receipts and the stored review claims. No code was changed and no Reviewer or
Codex run was made for this note.
Every statement marked **Source** comes from reading the files listed under
"Files read". Values quoted from receipts and claims are fixed words, numbers
and identifiers only. Anything not decidable is marked **UNKNOWN**.

## Question

Formal reviews through llmtool are refused `OUTPUT_NOT_JSONL`. The refusal
receipt shows `reply_diagnostic` with `kind=text`, `lines=1` and `cause=other`,
and the reply is about 190-250 bytes. No Codex session file is found. The same
account and model work through a direct `codex exec`. Which code paths in the
formal review dispatch can produce such a refusal, which of them match the
evidence, and what is the most likely cause?

## Evidence as given in the Task

- Issue #2486 comments c7068744797, c7068531229, c7066903506 (read in the
  fact-check revision).
- Refusal receipts (`review-refusals/<task>-<head>-<version>.json`, fixed fields
  only):

| PR | Task | Head | Version | Recorded | `excerpt` | `reply_diagnostic` | `automatic_rerun` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| #2495 | 93f385d0 | e115bc90 | 1 | 18:32:16Z | `REPLY_SHAPE bytes=198 lines=1 first=letter` | `kind=text lines=1 bytes=198 first=letter` (no `cause`; written before the cause field existed) | absent |
| #2495 | 93f385d0 | 28be5945 | 2 | 20:21:15Z | `REPLY_SHAPE bytes=189 lines=1 first=letter` | `kind=text lines=1 bytes=189 first=letter cause=other` | absent |
| #2508 | f6e1b8bd | 4c99af82 | 1 | 21:35:22Z | `REPLY_SHAPE bytes=200 lines=1 first=letter` | `kind=text lines=1 bytes=200 first=letter cause=other` | absent |

- Version 2 of #2495 is not an automatic re-run (no `automatic_rerun`); it is a
  fresh review of a new head after the branch was updated with main.
- Review claims in the same window (all `review_kind=formal`,
  `account_mode=ROLES`, `account=codex2`): refused, state `UNKNOWN` with
  `failure_reason=OUTPUT_NOT_JSONL`: #2490 v4 (17:52Z), #2495 v1 (18:30Z),
  #2498 v1 (19:47Z), #2495 v2 (20:19Z), #2508 v1 (21:33Z). Completed Codex
  reviews with a stored verdict: #2490 v3 (17:17Z) and v5 (20:20Z), #2496 v1
  (19:37Z), #2500 v1 (20:02Z), #2498 v2 (20:16Z), v3 (20:48Z) and v4 (21:27Z).
  Refusals and successes interleave within minutes on the same role and
  account.

Still UNKNOWN: the environment, flags and working directory of the direct
`codex exec` that worked.

## Files read

- `tools/llmtool/llmtool/review_dispatch.py`
- `tools/llmtool/llmtool/review_result.py`
- `tools/llmtool/llmtool/task_advance.py`
- `tools/llmtool/llmtool/github_identity.py`
- `tools/llmtool/llmtool/providers/codex_cli.py`
- `tools/llmtool/llmtool/providers/cli_lifecycle.py`
- `tools/llmtool/tests/test_review_refusal_rerun_2486.py`
- `tools/llmtool/tests/test_review_reply_excerpt_2486.py`

## Model-run state: what the source can and cannot show (F2)

The Task premise is "a one-line reply without a Codex model run". The source
cannot establish whether a model ran for any candidate below.

- `review_dispatch._dispatch_codex` sees only the exit status, stdout and
  stderr of the child. It has no field for "a model request was made".
- An `agent_message` event in stdout proves only that a line of that shape was
  in stdout. It does not show which part of the Codex CLI emitted it, or that a
  model produced its text. So the model-run state is **UNKNOWN** for the stream
  case (C4) as well as for the plain-line case (C1).
- A missing session file does not decide it, see fact 8.
- The one fixed value that bears on it is `stdout` content shape, which the
  receipt `excerpt` gives (see the check below). Even then it narrows the
  candidate; it does not state a model-run fact.

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
   reached. A line that is valid JSON but not an object (for example a bare
   quoted string) does not raise it; it is skipped.
4. **Two receipt branches.** `review_dispatch._parse_codex_review` first calls
   `_codex_final_message`.
   - **Branch A** (no usable final agent message): the diagnostic is computed
     over the whole stdout by `_reply_diagnostic_for_raw`, and the receipt
     `excerpt` is the `OUTPUT_SHAPE ...` word string from `_jsonl_shape`.
   - **Branch B** (a readable agent message came before the bad line): the
     diagnostic is computed over that last agent message by
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
   trailing one. In branch A that is the whole of stdout, so stdout was one
   unterminated line. In branch B it is the last agent message only; stdout as
   a whole held at least the event lines around it. Whether a CLI notice would
   normally end in a newline is UNKNOWN from the source; if it would, branch A
   is the less natural reading of `lines=1`.
6. **What `kind=text` means.** The text did not parse with the strict JSON
   reader and does not start with a code fence. A one-line JSON object with a
   duplicated key also gives `kind=text`, because the same duplicate-key hook
   is used.
7. **What `cause=other` means.** `review_dispatch._reply_cause` returns `other`
   when none of four closed word lists matched (case-insensitive, whole
   words): usage or rate limit (`rate limit`, `usage limit`, `quota`,
   `too many requests`, `429`), context or input size (`context length`,
   `context window`, `context limit`, `input too long`, `prompt too long`,
   `maximum context`, `request too large` and close forms), authentication
   (`unauthorized`, `authentication`, `auth failed`, `not logged in`,
   `log in`, `login`, `401`, `403`, `invalid api key`, `missing api key`,
   `credential`) and model unavailable (`model unavailable`, `model not found`,
   `model not available`, `no such model`, `overloaded`, `service unavailable`,
   `502`, `503`, `capacity`). So `other` rules out those exact words. It does not rule out
   the same meaning in other words, for example "limit" alone, "credits",
   "plan", "upgrade", "exceeded", "sign in", "expired", "try again later",
   "trusted directory" or "not a git repository". The cause is computed only
   for a `kind=text` reply of at most one line.
8. **`--ephemeral` is in the argv.** `review_dispatch._codex_argv` passes
   `--ephemeral`. The Codex thread id used for the claim comes from the
   `thread.started` event in stdout, never from a session file. By the flag's
   name, a successful review would also leave no session file, so "no session
   file" probably does not separate a refused run from a successful one.
   What the flag does inside Codex is UNKNOWN from the source.
9. **No fallback and one re-run.** `task_advance._reviewer_fallback` only
   switches Reviewer for `ROLE_MODEL_NOT_ALLOWED`,
   `REVIEWER_ACCOUNT_UNCONFIGURED` and `REVIEWER_NOT_ALLOWED`; the blocked
   fallback `task_advance._blocked_fallback_reviewer` applies only at the
   reviewer-attention step. An `OUTPUT_NOT_JSONL` refusal gets no Claude
   fallback. Instead `task_advance._review_refusal_rerun` allows exactly one
   automatic re-run of the same head and contract through
   `review_dispatch.dispatch_review` with `refusal_rerun=True`. The re-run's
   receipt carries `automatic_rerun`; the first receipt gets a `rerun` key.
   `review_dispatch.mark_refusal_rerun` writes it before the re-run starts.
   A receipt that already has `rerun` or `automatic_rerun` is never re-run, and
   a changed Task contract stops the re-run with
   `REVIEW_RERUN_CONTRACT_CHANGED`.
10. **Which route runs.** `review_dispatch._select_reviewer` picks the Codex
    Reviewer when the Developer family is Claude and the Claude Reviewer
    otherwise. Only the Codex route has a stdout parser. A Codex claim ends
    `COMPLETED` with a stored `review_result`; a Claude claim ends `DISPATCHED`.
    So the route of the four successful reviews can be read from the stored
    claim state, which is a fixed field.

## Reviewer child environment versus a direct `codex exec` (F3)

Source: `review_dispatch._dispatch_codex`, `review_dispatch.dispatch_review`,
`github_identity.ai_github_env`, and, as an in-repo contrast,
`providers.codex_cli.CodexCLIProvider`. The direct command's own environment is
not stated in the Task, so each row says what the llmtool path does and what
that would change relative to an ordinary shell run. None of the effects on
Codex is known from the source.

| Item | llmtool reviewer child | Ordinary shell run | Effect on the reply |
| --- | --- | --- | --- |
| Environment base | Built from scratch: `PATH` (the directory of the `codex` found on the caller's `PATH`, then `/usr/bin`, `/bin`), `HOME`, `LC_ALL=C`, `NO_COLOR=1`, and `CODEX_HOME` only when the role's `config_dir` is set | The caller's whole environment | A variable the direct run relies on is absent in the child. Which one matters is UNKNOWN. |
| Variables not passed | `USER`, `LOGNAME`, `LANG`, `LC_CTYPE`, `TMPDIR`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, proxy settings, any `OPENAI_*` or other `CODEX_*` setting | Present if the shell sets them | The Codex provider in `providers.codex_cli` does forward `USER`, `LOGNAME`, `LANG`, `LC_CTYPE`, `SSL_CERT_*` and sets `TMPDIR`; the reviewer path forwards none of them. UNKNOWN whether Codex needs them. |
| Account directory | `CODEX_HOME` is set to the role's `config_dir` when the role has one (`account_mode=ROLES`), else not set (`account_mode=CODEX_CLI`), so the default Codex home applies | The caller's `CODEX_HOME` or the default | Login or usage state in a different directory can differ from the direct run, even for "the same account". The claim keeps `account_mode` and `account` as fixed fields. |
| Locale | `LC_ALL=C` forced, no `LANG` | The user's locale | Effect on Codex output UNKNOWN. |
| GitHub and Git variables | `github_identity.ai_github_env` drops every name starting `GH_`, `GITHUB_`, `SSH_`, `GIT_TRACE` and several `GIT_*` names, then sets `GH_CONFIG_DIR` to the AI account's config directory and `GIT_CONFIG_COUNT=3` with a credential helper `!gh auth git-credential` and a URL rewrite | Caller's own | It touches no Codex-specific name (the base has none to drop). The credential helper needs `gh` on a `PATH` that holds only the `codex` directory, `/usr/bin` and `/bin`. On the Owner Mac at the fact-check, `codex` resolves to a Node launcher in the nvm `bin` directory (with `node` beside it) and `gh` is in `/usr/local/bin`, so `gh` is not on the child `PATH` there. This is the same for every Codex review, so it cannot by itself separate refused from successful reviews. |
| `dispatch_review` environment | `dispatch_review` builds a copy of the caller's `os.environ` with `CLAUDE_CONFIG_DIR` handled, but that copy is used only by the Claude route. The Codex runner builds its own environment | n/a | The copy does not reach the Codex child. |
| Flags | `--no-daemon`, `--ask-for-approval never`, `exec`, `--ignore-user-config`, `--ignore-rules`, `--ephemeral`, `--strict-config`, `--color never`, `--json`, `--model`, `-c model_reasoning_effort=...`, `-s read-only`, `--cd <Reviewer D6>`, prompt on stdin (`-`) | Probably a plain `codex exec "..."` | "Works through a direct `codex exec`" does not say the same flags worked. `--ignore-user-config` makes the user's Codex config unused, `--json` changes the stdout format to events, and `--strict-config` is a stricter parse. Their effects are UNKNOWN. The provider path in `providers.codex_cli` additionally passes `--skip-git-repo-check`; the reviewer path does not. |
| Working directory | The Reviewer D6, a fresh Git worktree made per review by the lifecycle script | The caller's directory | Whether Codex reacts to a fresh worktree is UNKNOWN. |
| Terminal | stdin, stdout and stderr are all pipes | Usually a terminal | UNKNOWN whether Codex prints differently without one. |
| Input | The rules prefix from `review_dispatch._review_rules` plus the rendered review prompt, over stdin | Likely a short prompt | See C3. |
| Executable | `shutil.which('codex')` on the llmtool process's `PATH`, then a `PATH` built around that directory | The shell's `PATH` | A different `codex` install could answer if the two `PATH` values differ. |

What this does for the evidence:

- Every row is the same for each reviewer run of one role, except three things
  that can vary between runs: the caller's `PATH` and `HOME` (when llmtool is
  started from different places), the role's `config_dir` (when the roles file
  changes), and the state held in the Codex home (login, token refresh, usage
  window), which changes with time and use.
- A static difference (a dropped variable, a flag) would fail every Codex
  review. The stored claims show the successful reviews in the same window
  were Codex runs (`COMPLETED` with a verdict) with the same `account_mode`
  and `account` as the refused ones. Static differences are therefore
  **contradicted as the sole cause**.
- Refusals and successes interleave within minutes on that same account
  (for example #2498 v2 succeeded at 20:16Z, #2495 v2 was refused at 20:19Z,
  #2490 v5 succeeded at 20:20Z). A Codex-home state that changes with time
  (login, usage window) is therefore also unlikely as the sole cause.
- What still varies per review is the input: the PR, its head and the rendered
  prompt (C3).

## Candidate paths

Each entry gives where the reply originates, the text shape it would produce,
and whether the evidence confirms, contradicts or cannot decide it.

### C1. The Codex process prints one unterminated plain line to stdout and exits 0

- **Where:** the external `codex` process started by
  `review_dispatch._dispatch_codex`. Source cannot say what it prints or why.
- **Shape:** branch A, `OUTPUT_SHAPE` excerpt for one bad line of one,
  `kind=text`, `lines=1`, `cause` from the four word lists, `first` probably
  `letter`.
- **Model-run state:** UNKNOWN. The source shows only that this text was on
  stdout.
- **Match to evidence:** the diagnostic fields alone (exit 0, one line, no
  newline, screen passed, `cause=other`) would fit, but the `excerpt` prefix
  does not (see the verdict).
- **Verdict:** **contradicted by the receipts.** All three stored receipts
  have an `excerpt` beginning `REPLY_SHAPE`, which only branch B writes
  (fact 4). Branch A would have written `OUTPUT_SHAPE`.

### C2. Environment, flags or account differ between the llmtool child and the direct run

- **Where:** the child environment and argv from the table above, as built in
  `review_dispatch._dispatch_codex` and `github_identity.ai_github_env`.
- **Shape:** a one-line CLI notice as in C1, or the output of a different
  Codex mode (`--json` events instead of text).
- **Match to evidence:** the table lists real differences (a minimal
  environment, `CODEX_HOME` set from the role, user config ignored, strict
  config, `--json`, a D6 worktree as the working directory, pipes). The Task's
  direct run does not show it used the same ones. If the Codex home of the
  role is a different login or usage state from the one the direct run used,
  "same account" in the Task may not hold for the child. A purely static
  difference fails every Codex review; a Codex-home state difference can vary
  with time.
- **Verdict:** **contradicted as the sole cause.** The stored claims show the
  successful reviews were Codex runs with the same `account_mode=ROLES` and
  `account=codex2`, interleaved within minutes with the refusals, so neither a
  static difference nor a time-varying account state explains which reviews
  fail. A difference can still be a necessary condition together with C3.

### C3. The input is PR-dependent: a stdin prompt the CLI answers with a one-line notice

- **Where:** stdin is the rules prefix from `review_dispatch._review_rules`
  plus the rendered review prompt from `task_submission_cli`, whose source is
  outside the files read. PR-dependent content or size is possible; the prompt
  size is not stored in the claim or the receipt.
- **Shape:** one-line CLI notice, as in C1. The input-size word list would
  catch common size wording, but not every wording.
- **Match to evidence:** #2495 was refused on two different heads (e115bc90
  and 28be5945) about two hours apart, while other PRs on the same account
  passed in between. #2490 and #2498 were each refused once and then passed on
  later heads. This follows the PR and its input more than the time.
- **Verdict:** **most consistent with the records**, but not proven: the
  prompt and its size are not stored, so which input property triggers it is
  UNKNOWN.

### C4. A readable stream with an agent message, followed by one bad line (branch B)

- **Where:** the stream itself. `review_dispatch._parse_codex_review` then
  reports the diagnostic of the last agent message, not of stdout. The test
  `test_jsonl_stream_with_bad_line_keeps_content_free_diagnostic` names this
  "the PR #2484 form".
- **Shape:** a `REPLY_SHAPE` excerpt. `kind=text`, `lines=1` and `cause=other`
  would describe a one-line plain `agent_message` item. `lines=1` fits this
  branch without the trailing-newline question of C1, because it counts the
  message text, not stdout.
- **Model-run state:** UNKNOWN. An `agent_message` item in stdout does not by
  itself show a model produced its text; the source cannot say which part of
  the CLI emitted it (F2).
- **Match to evidence:** compatible only if the `excerpt` begins with
  `REPLY_SHAPE`. It also needs a non-JSON line inside an otherwise valid
  stream. The test name shows this form occurred before (PR #2484), but the
  source does not say why a non-JSON line appeared. A clean stream with a
  plain final message would instead end as `VERDICT_NOT_JSON`, not
  `OUTPUT_NOT_JSONL`.
- **Verdict:** **confirmed as the form** by the stored receipts: every
  `excerpt` begins `REPLY_SHAPE`, so stdout held a readable stream whose last
  agent message was one plain-text line (189-200 bytes, starting with a
  letter, matching none of the cause word lists), followed by a line that is
  not JSON. Why that line appeared, and why the message was one line, is
  UNKNOWN. The model-run state stays UNKNOWN (F2).

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
  fixed wrong executable would fail them too (if they used Codex). A caller
  whose `PATH` varies between runs would not.
- **Verdict:** **contradicted as a fixed cause**: the successful reviews are
  Codex runs through the same lookup. A caller `PATH` that varies between
  `task advance` calls is UNKNOWN but not indicated by any record.

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
  Neither is on the formal review path; the provider is used above only as an
  in-repo contrast for environment and flags.
- **The injected `codex_runner` argument of `dispatch_review`:** used by tests
  only; production uses `_dispatch_codex`.
- **The Claude Reviewer route:** `review_dispatch._dispatch` returns a session
  id from the bridge and has no stdout parser, so it cannot yield this
  refusal.
- **`review_result` and `github_identity` as reply sources:** `review_result`
  classifies posted comments and never reads Codex stdout; `github_identity`
  only builds the environment (see the table). Neither produces reply text.
- **`task_advance` itself:** it only re-runs the same dispatch (fact 9) and
  produces no reply text. The #2495 version 2 receipt has no
  `automatic_rerun`, so it is a fresh review of a new head, not a re-run; the
  same PR failing on two heads favours C3 over a one-off.

## Most likely cause

**Form (from the records): branch B, C4.** Each refused review's stdout was a
readable Codex event stream whose last agent message was one plain-text line
(189-200 bytes, starting with a letter, no cause word matched), followed by a
line that is not JSON. The parser then refuses `OUTPUT_NOT_JSONL` at that line.

**Trigger (inference, not a source fact): input-dependent (C3).** The
successful reviews in the same window ran the same route, role and account
with the same environment and flags, interleaved within minutes, so neither a
static difference (C2 static part, C6) nor a time-varying account state
explains which reviews fail. What differs per review is the PR and its rendered
prompt. #2495 failed on two heads; #2490 and #2498 failed once and then passed
on later heads.

What the records and source cannot give: the text of the one-line message, the
content of the non-JSON line, why the stream contained that line, whether a
model produced the message (F2), and which property of the input triggers it.
The word lists leave most limit, plan and sign-in wordings in `other`, so
`cause=other` does not show the line is unrelated to a limit or login problem.

## Smallest observable check

No step below displays, copies, logs or stores any Reviewer or Codex output,
and none puts text from it in a comment, receipt or note (F1). Steps 2 and 3
reuse only the closed, fixed-word diagnostic the dispatch already builds.

1. **No new run: read the stored fixed fields first. Done in the fact-check
   revision** (see "Evidence"): every `excerpt` begins `REPLY_SHAPE` (branch B,
   C4); no receipt has `automatic_rerun`; all refused and successful claims are
   Codex formal reviews with `account_mode=ROLES`, `account=codex2`.
2. **One content-free replay by the PM, with the output captured, never shown.**
   Run the command `review_dispatch._codex_argv` builds for the refused head,
   with the environment `_dispatch_codex` builds, with `stdout` and `stderr`
   redirected to a private file or pipe that no one reads. Report only these
   values:
   - the exit status;
   - the byte count and newline count of stdout, and the byte count of stderr;
   - the closed values the dispatch already derives from stdout:
     the `OUTPUT_SHAPE` words (`line` number and count, `bytes`, `first`) and
     the `reply_diagnostic` fields `kind`, `lines`, `bytes`, `cause`,
     all obtained through the same screen gate as
     `review_dispatch._reply_diagnostic_for_raw`; a screen refusal is reported
     as `REVIEW_OUTPUT_WITHHELD` and nothing else.
   Add the `OUTPUT_SHAPE` words of the first bad line (`_jsonl_shape`), which
   give its position (`line=N of M`), byte count and `first` class without any
   text, so the place of the non-JSON line in the stream becomes visible. If it
   gives the same `REPLY_SHAPE` form with exit 0, the refusal reproduces. The
   replay is one more Reviewer run on the same prompt, so it is a PM decision
   that touches quota and sends the same private prompt as the review did.
3. **Separate the sub-causes without reading text.** If step 2 reproduces it,
   repeat with exactly one change at a time and record the same fixed values
   each time: (a) the caller's full environment instead of the minimal one,
   which tests C2's variable rows; (b) the default Codex home instead of the
   role's `config_dir`, or the reverse, which tests the account row;
   (c) without `--ignore-user-config`, then without `--json`, which tests the
   flag row; (d) a minimal prompt in place of the real one, which tests C3.
   The first change that turns the output into an event stream (newline count
   above zero and `kind` no longer `text`) names the sub-cause. If a variant
   still refuses, the unchanged items are not the trigger.
4. **Optional side check.** The file the dispatch writes at
   `.local/state/llmtool/codex-quota.json` under the user's home
   (`_record_codex_quota`) keeps the newest `used_percent`, `window_minutes`
   and `resets_at` seen in any JSONL stream. Compare its `read_time` and
   numbers with the refusal times. `_record_codex_quota` runs before parsing,
   and a branch B stream contains parsable events, so a refused run may update
   the file. Whether Codex emits those figures is UNKNOWN, so an absent or
   stale file decides nothing.

Follow-up (not done here, outside this Task's write scope): the receipt could
store the child's exit status and the stdout and stderr byte counts, and a
wider fixed-word cause list (for example plan, credits, sign-in, trusted
directory), all as fixed words or numbers with no text, so the next refusal
answers step 2 without a replay and without anyone reading output.

## Not decidable from the source (UNKNOWN)

- The text of the one-line agent message, the content and position of the
  non-JSON line, and why the stream contained it with exit 0.
- Whether a Codex model run took place in any refused review.
- What `--ephemeral`, `--strict-config`, `--ignore-user-config` and `--json`
  do inside Codex, and what Codex does for a fresh Git worktree, a pipe-only
  terminal or `LC_ALL=C`.
- What the rendered review prompt (from `task_submission_cli`) adds to stdin.
- Which property of the PR or rendered prompt triggers the refusal (C3).
- The environment, flags and directory of the direct `codex exec` that worked.
