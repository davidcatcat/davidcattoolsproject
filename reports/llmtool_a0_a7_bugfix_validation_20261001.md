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

## Validation

- Focused regression modules: 290 tests passed across input, terminal, CLI, chat, config,
  configure, context packet, validator, Claude, Codex, OpenAI and compatible-provider tests.
- Canonical command: `make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"` — exit 0;
  371 discovered, 370 passed, 0 failed, 0 errors, 1 existing host-filesystem skip for a
  non-UTF-8 filename; test runtime 40.239 seconds. Syntax compile and complete suite passed.
- `git diff --check` — PASS.
- Python 3.9 interpreter — unavailable on this host. Python 3.9 grammar validation with
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
