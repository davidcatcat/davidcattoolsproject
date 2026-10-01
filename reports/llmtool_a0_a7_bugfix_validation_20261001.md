# llmtool A0–A7 review bugfix validation — 2026-10-01

Review source: [`llmtool_code_review_20261001.md`](llmtool_code_review_20261001.md), preserved unchanged.

This branch fixes reproducible A0–A7 runtime and validator defects only. It does not
implement A8, change the A7 branch, execute suggested commands, add automatic approvals,
or introduce orchestration. Findings below use the review's original identifiers.

## Medium findings

| Finding | Status | Implementation and regression | Result |
|---|---|---|---|
| M1 — explicit input reads stdin | FIXED | `llmtool/inputs.py::read_input`; `test_inputs.py::test_explicit_text_never_inspects_stdin`, `test_explicit_file_never_inspects_stdin`, stdin-only and closed-stream tests; CLI pipe regressions in `test_cli.py` | Text and file inputs bypass stdin; stdin is read only when both are absent. Closed input has a sanitized error. |
| M2 — terminal escape/control spoofing | FIXED | New `llmtool/terminal.py`; ask-family and context-pack display boundary in `cli.py`, chat/history in `chat.py`; `test_terminal.py`, `test_chat.py::test_tty_reply_and_history_escape_controls_but_keep_stored_data`, `test_cli.py::test_model_output_escaped_only_for_tty_human_ask`, and `test_context.py::test_context_packet_tty_escape_preserves_json_data` | Interactive human output visibly escapes ESC/CSI/OSC, BEL, C1, CR, remaining C0 controls and bidi controls. Newlines, tabs and ordinary Unicode remain. JSON, stored packet values and redirected output are unchanged. |
| M3 — invalid inactive provider names | FIXED | `config.py::_validate` and `_apply_project`; `configure.py::run_configure`; `test_config.py::test_invalid_inactive_provider_name_is_rejected_without_losing_valid_profiles`, `test_configure.py::test_invalid_provider_name_fails_before_secret_prompt_or_config_write` | Every profile key uses `_provider_name`; configure validates the selected profile immediately, before secret input or writes. Failed validation leaves existing config intact. |
| M4 — arbitrary raw Git reads marked SAFE_READ | FIXED | `validator.py::_simple`; `test_validator.py::test_raw_git_reads_are_not_approval_free` | Textual `git ...` reads are UNKNOWN and require approval. A5 `llmtool git` fixed-argv collection is unchanged. |
| M5 — sudo inherits SAFE_READ | FIXED | `validator.py::_simple`; `test_validator.py::test_unknown`, `test_destructive`, and `test_review_hardening_cases` | Simple sudo forms use an UNKNOWN risk floor; sensitive, mutating and destructive inner risks remain higher. Unsupported sudo options remain UNKNOWN. |
| M6 — Claude descendant processes retain pipes | FIXED | `providers/claude_cli.py::_invoke`, `_collect`, `_kill`; `test_claude_cli_provider.py::test_descendant_holding_pipes_is_killed_after_leader_exits`, timeout and overflow tests | Claude starts a new session, kills its POSIX process group on timeout/overflow, and reaps the leader. The environment is allowlisted and cwd is an empty temporary directory outside the repository. |
| M7 — OpenAI Responses stored by default | FIXED | `providers/openai.py`; `test_openai_provider.py::test_request_uses_responses_schema_and_fixed_official_origin` | Official Responses requests send `store: false`; compatible-provider protocol is unchanged. |

## Related Low findings and directly adjacent confirmed nits

| Finding | Status | Implementation and regression | Result |
|---|---|---|---|
| L1 — validator underestimates destructive commands | FIXED | `validator.py::_simple`; `test_validator.py::test_review_hardening_cases` | Recognized destructive abbreviations and forms (`reset --har`, `clean --forc`, `branch -Df`, force/delete push, kubectl global namespace, sudo absolute path, `rmdir`, `unlink`, `shred`, `dd of=`, `find -delete`, `docker rm`) receive DESTRUCTIVE; `sed -i` is MUTATING. Unclear parsing stays UNKNOWN. |
| L2 — CR/C1/bidi in packet suggestions | FIXED | `validator.py::classify_command`; `terminal.py`; `test_validator.py::test_controls_and_bidi_are_never_safe`, `test_context.py::test_context_packet_tty_escape_preserves_json_data` | Unsafe formatting controls cannot lower command risk; the historical CR-spoof command remains DESTRUCTIVE. Human display escapes it; packet/machine data is retained. |
| L3 — Claude inherits environment and repository cwd | FIXED | `providers/claude_cli.py::_child_environment`, `_temporary_directory`; `test_claude_cli_provider.py::test_child_environment_is_allowlisted_and_cwd_is_isolated` | Child receives only PATH, identity/locale/TLS variables and the explicitly selected Claude auth HOME. Unrelated keys, Node injection and Git overrides are omitted. No auth files are inspected. |
| L4 — relative `which()` result can select local binary | FIXED | Claude and Codex `complete`; respective `test_relative_executable_resolution_is_rejected` tests | Both providers reject a non-absolute resolved executable path. |
| L5 — Codex killpg EPERM | FIXED | `providers/codex_cli.py::_kill`; `test_codex_cli_provider.py::test_killpg_permission_error_falls_back_without_replacing_timeout` | PermissionError falls back to direct kill and reap without replacing the originating timeout/overflow result. |
| L6 — API key validation mismatch | FIXED | `config.py::_validate_provider`, `api_key`; `test_config.py::test_api_key_whitespace_rejected_during_config_and_runtime` | Saved literals and runtime values reject empty, whitespace and non-printable keys consistently; errors omit key contents. Explicit `api_key_env` lookup remains supported and validated. |
| L7 — closed stdin/configure traceback | FIXED | `inputs.py::read_input`, `configure.py::_answer` and secret-input boundary; `test_inputs.py::test_closed_stdin_errors_are_sanitized`, `test_configure.py` noninteractive/invalid-input tests | Closed/broken input returns sanitized LLMToolError, no traceback or partial config write. |
| L8 — chat input exit status and over-generic errors | FIXED | `chat.py::run_chat`, `ChatSession.send`; `test_chat.py::test_chat_io_failure_returns_nonzero_without_traceback`, `test_incomplete_config_allows_offline_shell`, `test_known_provider_errors_hidden` | Chat input I/O returns 1. Safe provider LLMToolError messages are preserved; unexpected transport exceptions remain sanitized. |
| L9 — broken stdout pipe noise | FIXED | `cli.py::main`; `test_cli.py::test_broken_pipe_is_suppressed_at_cli_boundary` | BrokenPipeError is handled before OSError; stdout is detached and the interpreter does not emit flush noise. |
| L10 — unbounded timeout setting | FIXED | `config.py::_timeout_seconds`, `MAX_TIMEOUT_SECONDS`; configure uses the same validator; `test_config.py::test_timeout_seconds_finite_upper_bound` | Shared maximum is 3600 seconds; nonpositive, non-finite and larger values fail before provider use. |
| Nit — OpenAI-compatible `error: null` | FIXED | `providers/openai_compatible.py`; `test_provider.py::test_null_error_field_is_not_an_api_error` | Only a non-null error field is an API error. |
| Nit — prompt encoding after Claude spawn | FIXED | `providers/claude_cli.py::_invoke`; `test_claude_cli_provider.py::test_prompt_encoding_fails_before_spawn` | UTF-8 encoding occurs before Popen; invalid text cannot leave a child behind. |

## Review findings not changed

| Finding | Status | Reason |
|---|---|---|
| L11 — unrelated files in skill resource directory | DEFERRED | Independent skill discovery behavior; not required by the listed A0–A7 runtime hardening fixes. |
| L12 — `PYTHONUSERBASE` in test subprocess environment | DEFERRED | Test-environment portability item, independent of runtime defects. Canonical tests passed in the current environment. |
| Nit — slow-trickle responses can exceed an end-to-end wall-clock duration | DEFERRED | Existing HTTP timeout contract is a per-socket-operation timeout; this change bounds configured values but does not redesign transport deadlines. |
| Nit — config show masks a key when only `api_key_env` is configured | DEFERRED | Existing display behavior is safe and unrelated to the reproduced validation mismatch. |
| Nit — empty provider/model environment overrides | DEFERRED | Independent precedence semantics. |
| Nit — unquoted YAML provider names such as `2024` / `on` | DEFERRED | YAML authoring compatibility issue; strict provider-name validation remains unchanged. |
| Nit — configure asks questions before rejecting a symlink target | DEFERRED | Existing save path still refuses symlink replacement and writes nothing; prompt ordering is outside this pass. |
| Nit — chat input rejects selected controls and slash-prefixed messages | DEFERRED | Input semantics are separate from terminal-safe rendering of model-controlled output. |
| Nit — skills BOM handling and lstat/open race | DEFERRED | Independent skill loader concerns, outside the selected shared code paths. |
| Nit — GitDiff `truncated` is always false | DEFERRED | A5 behavior was not changed; no A5 redesign in this pass. |
| Uncertain — config ownership enforcement | NOT REPRODUCED | The original review did not establish a defect or documented contract; no behavior change. |
| Uncertain — config fsync / hard-link behavior | NOT REPRODUCED | The original review did not establish a failing filesystem case. |
| Uncertain — older Claude CLI flag support | NOT REPRODUCED | No compatibility failure was demonstrated; provider remains fail-closed on CLI errors. |
| Uncertain — alternate OpenAI-compatible finish reason | NOT REPRODUCED | No gateway incompatibility was demonstrated; protocol behavior is unchanged. |
| Uncertain — special `core.worktree` behavior | NOT REPRODUCED | The observed case failed closed; A5 was not modified. |
| Uncertain — automatic secret redaction | OUT OF SCOPE | The documented product contract does not claim secret redaction; no automatic retrieval or redaction policy was added. |

## Initial author validation (historical)

- Focused regression modules: 290 tests passed across input, terminal, CLI, chat, config,
  configure, context packet, validator, Claude, Codex, OpenAI and compatible-provider tests.
- Canonical command: `make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"` — exit 0;
  371 discovered, 370 passed, 0 failed, 0 errors, 1 existing host-filesystem skip for a
  non-UTF-8 filename; test runtime 40.239 seconds. Syntax compile and complete suite passed.
- `git diff --check` — PASS.
- Initial author validation: Python 3.9 interpreter was considered unavailable at that time. Python 3.9 grammar validation with
  `ast.parse(feature_version=(3, 9))` passed for all 36 llmtool package and test Python files;
  `pyproject.toml` still declares `requires-python = ">=3.9"`.
- Wheel build — PASS using isolated setuptools build dependencies. The resulting wheel
  contains byte-identical `llmtool/terminal.py`; no dependency definitions changed.
- Network/live-provider tests — NOT RUN. Mock HTTP tests use loopback only; OpenAI regression
  used a local stub and made no paid request. Claude/Codex subprocess regressions used fakes.
- Security source review — `validator.py` contains no subprocess, shell, Git, Kubernetes,
  Terraform, eval or exec path. Suggested commands remain data. A5 implementation was not
  edited; no A7 files were modified and no A8 implementation was added.

The A7 feature branch remains separate and untouched. This report does not claim this fix
branch or A7 is merged to main.


## Round 2 — 2026-10-01

Independent evidence: [Claude bugfix review](llmtool_a0_a7_bugfix_review_20261001.md),
preserved unchanged. Its actual Python 3.9.6 run completed 371 discovered tests
(370 passed, 1 skipped), superseding the initial author's grammar-only evidence.
M1–M7 fixes remain in place. Historical tables above describe the first pass;
this section corrects the incomplete L1 and overbroad M4 conclusions.

Synchronization: fetched origin, fast-forward pull was already current, then merged
`origin/main` `5bb8b7ea4576f4a9b24f6b2e8d9f1c1153bf6b5d` into the existing bugfix
branch without conflicts, rebase or force push. Merge commit:
`f26cb6f68352032f994d0b4b2fc2b8226932cbdd`.

| Remaining validator item | Status | Resolution |
|---|---|---|
| reset bare `--` | FIXED | Prefix matching requires more than two characters; `reset -- file.txt` and `reset -- .` are MUTATING; `--h`/`--ha`/`--har`/`--hard` remain DESTRUCTIVE. |
| clean bare `--` | FIXED | Same meaningful-prefix guard; `clean -- x` is UNKNOWN; `--forc`, `--force`, `-f` are DESTRUCTIVE. |
| force push `+refspec` | FIXED | Leading nonempty `+` refspecs are DESTRUCTIVE, including full refs and source:destination forms; ordinary refs and embedded plus characters remain MUTATING. |
| sed long in-place option | FIXED | `-i`, `-i.bak`, `--in-place`, `--in-place=.bak` are MUTATING. |
| checkout path restoration | FIXED | `checkout` with `--` followed by paths is DESTRUCTIVE; branch checkout remains MUTATING. |

ChatGPT content-read semantic regression: FIXED. Narrowly recognized full/staged/cached
`git diff`, full `git log`, and `git show`/`git show HEAD` are SENSITIVE_READ.
Raw Git metadata reads remain UNKNOWN. Both require approval, explicitly verified
in tests. Unsupported forms remain UNKNOWN; no complete Git parser was added.
All full-option `.startswith(user_option)` matches were inspected: only reset/clean
use this pattern, and both now exclude bare `--`. A5 hardened Git collection is unchanged.

Five new regression methods cover the remaining issues and content reads; the existing
table helper now checks approval semantics for every classified example. Historical
sensitive cat, safe metadata ls, unknown sudo ls and destructive sudo rm remain covered.

Root README stale A2.5 status: FIXED. A0–A6 merged; A7 and this bugfix branch unmerged.
Raw Git wording: FIXED. Historical pre-hardening Claude live PASS is retained and
explicitly separated from current hardened offline and live evidence.

### Current hardened Claude smoke

PASS: exactly one current live marker request through the canonical checkout's installed
`tools/llmtool/.venv/bin/llmtool` (version 0.1.0; imported package verified inside
this repository). Tested branch was `fix/llmtool-a0-a7-review-bugs`, base HEAD
`f26cb6f68352032f994d0b4b2fc2b8226932cbdd` plus Round-2 working changes;
`origin/main...HEAD` was 0 behind / 2 ahead. Provider source is unchanged from that HEAD.

- Prompt: `Reply with exactly: CLAUDE_HARDENED_OK` (newline after the colon).
- llmtool exit status: 0; stdout exactly `CLAUDE_HARDENED_OK` followed by newline.
- Executable: `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/claude`.
- Safe version output: `2.1.286 (Claude Code)`.
- Isolated runtime succeeded through the provider's mandatory empty temporary cwd,
  executable resolution, allowlisted environment, process lifecycle and normalization.
- Temporary llmtool configuration selected only claude-cli; existing Claude CLI session
  location was supplied via the provider's explicit runtime HOME override. No credential
  files were inspected, no authentication was changed, and no repository/private content
  was sent. Raw provider stderr and credentials were not recorded.

### L12 and CI

L12: FIXED in `test_cli.py` and `test_configure.py`. Temporary HOME remains isolated;
`PYTHONUSERBASE=site.getuserbase()` preserves access to the actual interpreter's user-site
packages without inheriting user configuration or credentials. Python 3.9 reproduced the
second subprocess failure before this fix and validates both paths afterward.

CI: ADDED in `.github/workflows/llmtool-ci.yml`, push/pull_request path filters include
`tools/llmtool/**` and the workflow itself. Ubuntu matrix: Python 3.9 and 3.14;
documented editable install followed by `make -C tools/llmtool check`. YAML syntax,
matrix, path filters and commands inspected. Read-only contents permission; no secrets,
live providers, API keys or VPN requirements; nlf workflows unchanged.
Local validation is not a GitHub Actions PASS. Remote execution status is recorded below.

### Final validation and review

- Focused validator: 47 discovered/passed, 0 failures/errors/skips; 0.300 seconds.
- Canonical Python 3.13.3: `make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"`, exit 0; 376 discovered, 375 passed, 0 failures, 0 errors, 1 existing non-UTF-8 filesystem skip; unittest elapsed 42.261 seconds.
- Actual Python 3.9.6: `make -C tools/llmtool check PYTHON=/usr/bin/python3`, exit 0; 376 discovered, 375 passed, 0 failures, 0 errors, 1 same skip; unittest elapsed 66.421 seconds. Complete suite, not grammar-only validation.
- Initial sandbox attempts encountered denied loopback binds and Python 3.9 bytecode-cache writes; approved host reruns were used. The first host Python 3.9 run reproduced the configure-subprocess L12 failure; after the second small environment fix, both complete suites passed.
- Wheel: PASS; rebuilt with declared isolated setuptools dependencies, byte-identical `validator.py`, `providers/claude_cli.py`, and `terminal.py`. Wheel SHA256: `7ecb3e5043659dc762507f5ebec83fb0b882cf070166fa8dab92015b53ffe115`.
- Documentation links and milestone wording checked; `git diff --check` PASS.
- Final production diff security review: no material findings. Validator remains data-only; no `shell=True`, `os.system`, eval/exec, automatic execution or Git mutation was introduced. No credential/raw provider stderr output or repository exposure to Claude. A5 and A7 implementations remain untouched; no A8 work.
- Remaining deferred findings: L11 and the unrelated nits/uncertain items in the historical tables remain deferred or out of scope. L12 is superseded by the FIXED result above. No security control was weakened for live validation.
- CI remote execution: PASS after pushing implementation commit `c096e851fd57cdb66087eb07758a224e7e53633b`; [run 36809882077](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/36809882077) completed successfully for both Python 3.9 and 3.14. This is actual GitHub Actions evidence, separate from local checks.

Stop for external ChatGPT review; do not merge this branch to main or advance milestones.


## External ChatGPT review — Round 2

External ChatGPT review: **PASS**.

Reviewed branch HEAD `02af9d22a3d419c4cda0116b6e007f55d7a9c1a3` against
`main` `5bb8b7ea4576f4a9b24f6b2e8d9f1c1153bf6b5d`.

Confirmed:

- M1–M7 remain fixed; no blocking correctness/security regression found.
- Round-2 validator fixes correctly cover bare `--`, force `+refspec`,
  long `sed --in-place`, destructive `git checkout -- <path>`, and preserve
  SENSITIVE_READ semantics for recognized content-bearing Git reads.
- Raw Git metadata reads remain approval-requiring UNKNOWN; A5 hardened Git
  Intelligence remains separate and unchanged.
- Root/llmtool documentation no longer uses stale A2.5 or overbroad raw-Git wording.
- Current hardened Claude CLI marker smoke is recorded PASS through llmtool.
- GitHub Actions run `36809882077` was independently checked: both
  `check (3.9)` and `check (3.14)` completed successfully for implementation
  commit `c096e851fd57cdb66087eb07758a224e7e53633b`.
- Current branch HEAD differs from that CI-tested implementation only by the
  validation-report status update; production/test/workflow files are unchanged.
- No A7 implementation or A8 work is included in this bugfix branch.

No blocking issue remains for merge review. Deferred historical nits remain explicitly
out of scope and do not invalidate this bugfix pass.
