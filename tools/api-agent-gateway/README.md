# API Agent Gateway (`gw-agent`), phase 1

A standalone tool that runs a bounded agent loop against an API-only chat model
inside one working directory. File tools are confined to declared paths. Design:
[api-agent-gateway-design-20261102.md](../../docs/reports/api-agent-gateway-design-20261102.md)
(section numbers below refer to it).

Phase 1 is an offline MVP. It has been tested only against a local fake server.
No live provider call has been made (live probes are phase 0). The package does
not import llmtool, and llmtool does not import it. The only coupling between
them is the CLI/JSON contract below. Python ≥ 3.11, stdlib only.

```sh
PYTHONPATH=tools/api-agent-gateway python -m api_agent_gateway --help
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tools/api-agent-gateway/tests
```

## Commands

```
gw-agent run    --authorization-file auth.json --workdir <D6 root> --profile <name> \
                --task-file task.json --result-file result.json --audit-file audit.jsonl
gw-agent resume <session_id> (same flags as run)
gw-agent sessions --json
gw-agent end <session_id> [--reason ended|task_terminal]
```

All four commands accept `--state-dir`, default `$GW_AGENT_STATE_DIR` or
`~/.local/state/api-agent-gateway`. They also accept `--profiles-file`, default
`$GW_AGENT_PROFILES` or `<state-dir>/profiles.json`. For `run` and `resume`,
stdout is not part of the contract. `sessions` and `end` print one JSON object.

## Authorization record (run and resume)

The gateway never decides on its own that egress is authorized. llmtool
re-checks the dispatch policy, the `egress_basis` (not withdrawn, expired or
superseded), the data class, the profile and that the Task is not terminal, then
writes a fresh record and passes it with `--authorization-file`. Both `run` and
`resume` require it, and both validate it before any provider call:

```json
{"schema_version": 1, "authorization_ref": "...", "checked_at": "2026-11-02T10:00:00Z",
 "scope_digest": "<64 hex>", "task_id": "...", "egress_basis": "...",
 "data_class": "...", "profile": "<profile name>", "session_id": null}
```

- `authorization_ref`: non-empty, at most 512 characters.
- `checked_at`: UTC, at most 15 minutes old and at most 60 seconds in the future.
- `scope_digest`: must equal the digest the gateway computes from this
  task.json (see Sessions). A record issued for a different scope does not
  authorize this one.
- `task_id`, `egress_basis`, `data_class`, `profile`: must equal the task's
  values and the `--profile` in use.
- `session_id`: `null` (or absent) for `run`; the session ID for `resume`.

A missing, malformed, stale or mismatched record is refused with
`RUN_AUTHORIZATION_INVALID` on `run` and `SESSION_AUTHORIZATION_INVALID` on
`resume` (exit 5). Without the flag at all, the CLI exits 2 (usage). The SHA-256
of the record is bound in the audit header (`authorization_sha256`).

## task.json

| Field | Rule |
|---|---|
| `schema_version` | `1` |
| `task_id` | `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` |
| `source_sha` | Must equal the workdir `HEAD`. The workdir must be the repository top level. |
| `goal`, `acceptance[]` | Text sent to the model, after screening |
| `read_paths[]`, `write_paths[]` | Relative files. A trailing `/` means a subtree. `.`, `..`, absolute paths and `.git` are refused. Write paths are also readable. |
| `egress_basis` | Required and non-empty (`EGRESS_BASIS_MISSING`), and must match the authorization record. |
| `data_class` | Must be listed in the profile's `allowed_data_classes`. |
| `budgets` | All of `max_turns`, `max_input_tokens`, `max_output_tokens`, `max_wall_seconds`, `max_tool_result_bytes`, `max_sent_bytes`, as integers. Budgets apply to each invocation. |
| `findings[]` | Optional review findings for a repair round |

Unknown keys are refused (`TASK_INVALID`).

## Profiles file

```json
{"schema_version": 1, "profiles": {"gw": {
  "adapter": "openai-chat-tools", "base_url": "https://host/v1", "model": "name",
  "auth_env": "ENV_VAR_NAME", "context_cap_tokens": 131072, "max_output_tokens": 16384,
  "max_length_retries": 2,
  "allowed_data_classes": ["synthetic"], "session_expiry_hours": 72,
  "timeout_seconds": 120, "tool_choice": "auto", "request_extra": {}}}}
```

- **`base_url`:** HTTPS is required, except plain HTTP to a loopback host or
  to a host explicitly named in the profile's optional `http_allow_hosts`
  list. Use `http_allow_hosts` only for company VPN endpoints: listed IP
  addresses must be private or link-local, while listed hostnames are allowed
  for VPN DNS names. Plain HTTP traffic is unencrypted and can be observed or
  changed by parties able to reach the network path. Public IP addresses are
  refused even when listed. The client uses no proxies and follows no
  redirects.
- **`auth_env`:** names the environment variable that holds the API key. The
  key is read at run time and never stored. It is also screened as a literal.
- **`session_expiry_hours`:** at most 72.
- **`request_extra`:** carries provider-specific switches, such as thinking
  on/off, until phase 0 decides them. For example,
  `{"chat_template_kwargs":{"enable_thinking":false}}` is sent unchanged on
  every request. It cannot override `model`, `messages`, `tools`, `tool_choice`,
  `max_tokens` or `stream`. The profile digest includes this object, so changing
  it prevents resuming a session created with the prior profile.
- **Output budget:** with thinking enabled, start with `max_output_tokens: 16384`
  and set the task's `max_output_tokens` budget to cover the expected replies
  and any budgeted truncation re-prompts. Thinking tokens count toward the
  provider's output limit and the gateway's output-token budget.
- **`max_length_retries`:** integer from 0 through 5, default 2. A response
  ending with `finish_reason: "length"` is audited, but its assistant message
  and any partial tool calls are discarded. The gateway adds a fixed user
  nudge and makes another normal, budgeted turn. The retry count is consecutive;
  any non-length response resets it. Once the configured retries are exceeded,
  the run ends with `BUDGET_EXCEEDED` / `OUTPUT_TRUNCATED` (exit 6). A retry is
  a budgeted turn, never a fallback model.
- **`adapter`:** `text-tools` is refused with `ADAPTER_NOT_IMPLEMENTED` in phase 1.
- **File safety:** the profiles file must be a regular file owned by the
  current user and not writable by group or others (`PROFILES_UNSAFE`).
- **Timeout:** `timeout_seconds` is a total deadline per call, including a
  slowly dripping response.

## result.json and exit codes

`result.json` is created with `O_EXCL`, mode 0600, outside the workdir. It
holds `status`, `code`, `session_id`, `run_id`, `task_id`, `changed_paths[]`,
`summary`, `served_model`, `usage` (`REPORTED` / `PARTIAL` / `UNKNOWN`),
`turns`, `budget_used` and `audit_sha256`.

| status | exit |
|---|---|
| `DONE` | 0 |
| `BLOCKED` (`MODEL_REPORTED_BLOCKED`, `NO_TOOL_CALL`) | 3 |
| `MODEL_ERROR` (`HTTP_<n>`, `HTTP_REDIRECT_REFUSED`, `MALFORMED_RESPONSE`, `TIMEOUT`, `TRANSPORT_ERROR`, `AUTH_ENV_MISSING`, …) | 4 |
| `POLICY_REFUSED` (`EGRESS_BASIS_MISSING`, `RUN_AUTHORIZATION_INVALID`, `DATA_CLASS_NOT_AUTHORIZED`, `SOURCE_SHA_MISMATCH`, `CONFINEMENT_VIOLATION`, `EGRESS_SCREEN_RESIDUAL`, `SESSION_*`, …) | 5 |
| `BUDGET_EXCEEDED` (`TURN_BUDGET_EXCEEDED`, `INPUT_TOKEN_BUDGET_EXCEEDED`, `OUTPUT_TOKEN_BUDGET_EXCEEDED`, `OUTPUT_TRUNCATED`, `WALL_TIME_EXCEEDED`, `SENT_BYTES_EXCEEDED`, `TOOL_RESULT_BYTES_EXCEEDED`, `CONTEXT_EXHAUSTED`) | 6 |
| usage error (no result file) | 2 |
| internal error | 1 |

Each budget has its own `code`. Length re-prompts are the only retry: they use
the same configured model and consume the ordinary turn, input, output, sent
byte and wall-time budgets. No fallback model is ever used.

## Sessions (section 5.1)

- **Binding.** A session is bound to:
  - the workdir identity (device and inode);
  - the recorded HEAD;
  - the profile name and the profile digest;
  - the `task_id`;
  - `scope_digest`, the SHA-256 of the canonical JSON of `read_paths`,
    `write_paths`, `egress_basis`, `data_class` and `budgets`.
- **Resume checks.** `resume` checks all of the following before any provider
  call:
  - lock, then workdir, profile and task (`SESSION_WORKDIR_MISMATCH`,
    `SESSION_PROFILE_MISMATCH`, `SESSION_TASK_MISMATCH`);
  - scope (`SESSION_SCOPE_CHANGED`);
  - the llmtool authorization record (`SESSION_AUTHORIZATION_INVALID`);
  - `HEAD` == `source_sha`, and `HEAD` descends from the recorded HEAD
    (`SESSION_HEAD_NOT_DESCENDANT`).

  The record format is the one described under Authorization record, with
  `session_id` set; its `scope_digest` must also equal the session's recorded
  digest.
- **Changes allowed on resume:** only `goal`, `acceptance` and `findings`.
- **Lock.** Each session has an `flock`. A second `resume` while one is running
  is refused with `SESSION_LOCKED`, not queued. So is an `end` while it is running.
- **Storage.**
  - State lives under the state directory: directories are 0700, files 0600.
    The state directory must not be inside a Git worktree.
  - Deletion happens on `end`, on `end --reason task_terminal` (llmtool calls
    this when the Task becomes terminal), and on expiry. Expiry is checked on
    every gateway start and by `sessions`.
  - Deletion leaves only a content-free tombstone.
- **Screening.** Every provider call screens the complete serialized request,
  never only the delta: system prompt, contract, retained history, compaction
  summary, findings and tool results.
- **Compaction.**
  - When the request plus `max_tokens` would exceed `context_cap_tokens`, older
    turn groups are replaced by a bounded, deterministic, screened summary. That
    summary lists the tool names and paths, not file contents.
  - The contract, the latest findings round and the latest turn are always kept.
  - Each compaction is recorded in the audit chain. If the request still does
    not fit, the run ends with `CONTEXT_EXHAUSTED`.

## Confinement (section 4.2)

- **Paths.** The workdir is opened once as a directory descriptor. Each
  component is opened with `O_NOFOLLOW` relative to its parent descriptor. The
  gateway refuses:
  - symlinks, `..`, absolute paths and `.git`, in any case;
  - FIFOs, sockets and devices;
  - files with `st_nlink > 1`;
  - cross-device directories;
  - anything outside the declared sets;
  - case or Unicode-normalization variants of an on-disk name
    (`NAME_MISMATCH`), because scope matching is exact while APFS is not.
- **Listings.** `list_dir` and `grep` show only entries that are in scope or
  lead to in-scope entries.
- **Writes.**
  - Writes go to a temp file in the same directory descriptor, followed by
    `renameat`. Creating a new file uses `linkat`, which never overwrites.
  - The directory chain is re-verified before and after the rename, and the
    final inode is checked.
  - If an identity changes, the run ends with `CONFINEMENT_VIOLATION`.
  - A new file's parent directory must already exist.

## Audit log (section 4.5)

- **Creation.** The log is created with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600,
  outside the workdir.
- **Records.** It is a canonical-JSON hash chain (`seq`, `prev_sha256`).
  - The header binds `run_id`, `task_id`, `source_sha`, the workdir identity,
    the profile and its digest, the budgets, the SHA-256 of `task.json`,
    `scope_digest`, the SHA-256 of the authorization record, and on resume
    the session ID.
  - `model_call`, `tool`, `compaction`, `refusal` and `end` records hold only
    counts, codes and digests: path digests, the SHA-256 of what was sent, and
    redaction counts.
- **`audit_sha256`.** `audit_sha256` in `result.json` is the digest of the final
  record. `api_agent_gateway.audit.verify(path)` re-checks the chain.
- **Limit.** The chain detects tampering after the fact. It does not protect
  against the same UID rewriting the whole log.

## Screening (section 4.3)

- **Order.** Screening happens after authorization, never instead of it. The
  minimal credential-shape set is copied from llmtool's diagnostics patterns,
  not imported. It covers:
  - private-key blocks, including a block with no END marker and bare PEM body
    lines;
  - AWS access keys, `sk-` keys, GitHub tokens and Slack tokens;
  - JWTs and Bearer/Basic values;
  - quoted credential assignments;
  - the profile's own API key as a literal.
- **Replacement.** Every match is replaced with `[REDACTED:credential]`.
- **Residual check.** A residual check runs on the serialized body. If a match
  remains, the run is refused with `EGRESS_SCREEN_RESIDUAL`.

## Phase 1 limits (deferred)

- **`text-tools` adapter:** stub only. Phase 0 probes decide whether it is
  needed.
- **Data-class content classification:** none. `data_class` is enforced as an
  authorization check against the profile. Read scope and credential screening
  are the content controls. Other "outside the data class" detection, such as
  customer data, is not attempted.
- **Not done in phase 1:**
  - no streaming;
  - compaction uses no model-written summary;
  - the gateway does not create directories.
- **Budgets:** apply to each invocation. They are not cumulative across a
  session's rounds. `scope_digest` stops any change to the ceilings, but total
  spend over many repair rounds is bounded only by how many rounds llmtool
  starts. Whether to make them per session is an open decision.
- **Concurrent same-UID attacker:** if another local process moves a parent
  directory out of the workdir during an edit, the write can land in the moved
  directory. The re-verification detects this and ends the run with
  `CONFINEMENT_VIOLATION`, but does not undo it. The model itself cannot cause
  this; it has no rename tool.
