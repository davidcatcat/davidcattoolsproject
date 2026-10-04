# A11a.5 — Offline runs / trace validation — 2026-10-04

Status: **CONTRACT ACCEPTED / IMPLEMENTATION AND VALIDATION PENDING**.
This checkpoint precedes source implementation. Only spec section 17 A11a.5 is
authorized. Overall A11a is incomplete; A11a.6/7, A11b/c and A12 remain unstarted.
No merge or later-slice work is authorized.

## Repository and agents

- Existing user-selected Codex Cloud checkout: `/workspace/davidcattoolsproject`.
- Origin: `https://github.com/davidcatcat/davidcattoolsproject.git`.
- Branch: `feat/a11a5-runs-trace-cloud-20261004`, created from exact fetched main
  `2bade6aa95963d3f2c3d9332aa3939407512da7e`; initial working tree clean.
- A11a.4 PR #19 merged at 2026-10-04T01:12:53Z, merge SHA above; its final
  feature head was `01db2eff3347835f0bf7b38bc227247bf41d0fb0`. Post-merge main CI run
  [37167325001](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37167325001)
  was independently verified by the coordinator on exact merge head `2bade6a`,
  event=push, completed/SUCCESS (Python 3.9/3.14 SUCCESS). This is base/main CI,
  not feature-head CI or A11a.5 candidate CI.
- Developer: `/root/a11a5_developer`; requested `gpt-6.1-sol`, reasoning high.
  Actual runtime model/reasoning: **UNVERIFIED** (no runtime metadata exposed).
- Reviewer: `/root/a11a5_reviewer`; requested `gpt-6-astra`, reasoning high;
  actual runtime model/reasoning **UNVERIFIED**. Exact source/frozen SHA and
  disposition will be recorded after independent review. The coordinator alone coordinates/accepts; Developer
  is sole writer; Reviewer never modifies files. At most two correction rounds.

Read AGENTS.md, WORKFLOW.md, root/tool README, ROADMAP, the full A11a spec,
A11a.4 validation and inspect-repo, implement-feature, review-change, safe-git,
validate-runtime skills. The historical spec's next-A11a.1 wording is historical;
its section 17 A11a.5 defines this slice. Applied cloud-environment-runtime;
current managed environment is running/connected, policy observations current,
HTTP unrestricted/enforced, no configured VPN/secrets/outbound identities.
Inherited proxy and CA trust remain intact.

## Accepted reader / output contract

Public commands are only `llmtool runs`, `llmtool trace <run_id>`, and trace-only
`--errors` / `--json` (combinable). No runs JSON flag or configurable reader limits.
Readers dispatch before config/skill/provider paths and use only the existing
pure home resolver; no user/project YAML, credential, auth/session, network or
provider resolution. Missing home/runs gives runs exit 0 with no rows; trace
missing run exits 1. Invalid ID exits 1 without echo. Usage errors remain 2,
interrupt 130, broken pipe 0. Failure stdout is empty and stderr contains a fixed
reader code/message, never an exception or path.

All opens are read-only, anchored and no-follow. Home/runs/run directories must
be effective-user-owned/private; files regular/private/single-link. Traversal
rejects symlinks and non-directory components. Existing root/run lock files are
opened read-only; nonblocking shared locks acquire root then run and release on
every path. Missing/unsafe locks in populated stores fail safely; active runs
are incomplete with no event bytes displayed. Identity/size/mtime checks detect
unstable snapshots. No store creation, cleanup, chmod/repair, file writes or
record mutation occurs. Unknown filenames are neither opened nor echoed.

| Constant | Bound / behavior |
| --- | --- |
| RUN_SCAN_LIMIT | 1,000 entries, including unrelated names; overflow fails closed |
| RUN_FILE_SCAN_LIMIT | 16 entries per run; overflow is damaged / fails trace |
| RUN_OUTPUT_LIMIT | 100 newest rows; started_at then run_id descending; null time last |
| SUMMARY_MAX_BYTES | 4 KiB per summary; runs reads summaries only |
| RUNS_READ_MAX_BYTES | 4 MiB aggregate summary bytes; no trace-content reads for listing |
| TRACE_MAX_BYTES | 8 MiB hard file/read bound; existing store maximum |
| EVENT_MAX_BYTES | 16 KiB per JSONL line |
| MAX_RUN_EVENTS | 32 events; whole trace validates before filtering |
| JSON structural bounds | depth 3, at most 64 values per document |
| Text bounds | 2 KiB general text, 1 KiB error message, 4 KiB cause/provider mapping |
| READER_OUTPUT_MAX_BYTES | 256 KiB; render fully before stdout, never partial JSON |
| HOME_COMPONENT_LIMIT | 128 absolute path components; excessive depth fails before opening |

Listing validates summary types/schema/ID and expected artifact security, but
does not claim that trace contents were checked. Damaged/unknown entries appear
only as bounded generic counts. No-summary entries are incomplete (null started
time for listing); active completed-looking entries remain incomplete. A stable
trace requires its summary completion marker and checks trace count/bytes/start/
end/mode/status/duration against it. Corrupt completed traces fail safely. Stable
partial traces expose only valid complete-line prefixes; a bounded trailing
fragment is omitted. Without summary they remain incomplete even with run.end.

Strict JSON rejects duplicate keys, nonfinite/constants, invalid UTF-8, controls,
recognizable credentials, excessive depth/items/bytes. Schema names and integer
version 1 are exact; unsupported versions return FORMAT_UNSUPPORTED. Required
v1 fields pass strict type/bound checks. Supported-version extensions, including
event data/cause extensions, validate within bounds then are ignored. Unknown
v1 event types project generic `unknown` metadata with empty data. Filtering
selects only recognized structured error/warning types, never message matching or
run.end severity, and occurs after validating the entire trace.

Internal runs result fields: `schema=llmtool.diagnostics.runs`,
`schema_version=1`, `runs`, `damaged_count`, `unknown_count`, `truncated`.
Public trace JSON fields: `schema=llmtool.diagnostics.trace`, `schema_version=1`,
`run_id`, `state` (`complete`/`incomplete`), `summary` (projected object or null),
`events`, `filtered` (boolean), `omitted_partial_line` (boolean).
Summary projection fields: run_id, command, mode, status, started_at, ended_at,
duration_ms, provider_type, final_error_code, event_count, dropped_event_count,
trace_bytes. Incomplete listing rows use null unavailable values and
status=incomplete. Event projection fields: event_id, timestamp, type, component,
severity, span_id, parent_span_id, data. Known event data contains only closed
run/provider/error fields and bounded numbers/booleans; arbitrary stored
message/model/profile strings are omitted. Component is a fixed known application
or provider component, otherwise `unknown`; unknown type is `unknown`, data={}
and component=unknown. Human rendering uses the same projection. No raw document,
prompt/response, path, credential, exception, terminal controls or unknown names
are rendered.

## Acceptance gates declared before implementation

Required Cloud checks: public CLI tests for saved/active/partial runs, exact trace
JSON/filter/newest ordering/truncation; complete strict schema/extension/bounds
matrix; symlink/owner/mode/hardlink/FIFO/directory/traversal tests; deterministic
replacement/truncation/publication/cleanup races; byte/name/mode/mtime snapshots
and mutation-call guards including absent/unsafe/damaged stores; zero config,
credential/provider/network/subprocess/auth-file access proofs. Focused reader,
diagnostics and affected CLI regressions; canonical compileall/full unittest;
installed offline CLI smoke and wheel/resource provenance; docs/links/status,
privacy/scope and diff review. Independent read-only review precedes publication;
exact final published documentation head Python 3.9/3.14 CI must pass.

Canonical command (from this checkout):

```sh
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
```

Existing prospective test-only `/var/tmp` allowance covers empty Codex fixture
runtime directories; existing additional network allowance covers loopback mock
tests. No live provider, Mac/VPN/Claude session or Windows backend prerequisite
is required by this offline reader slice, and no such PASS will be claimed.
Implementation, command/results, exact SHA, review and publication evidence will
be appended after execution. User-authorized commit/push/draft PR are deferred
until independent review converges; merge remains forbidden.
