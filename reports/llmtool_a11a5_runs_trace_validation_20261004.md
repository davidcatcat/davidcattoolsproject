# A11a.5 — Offline runs / trace validation — 2026-10-04

Current status: **RESUMED / CORRECTION ROUND 1 CLOUD CHECKS PASS /
INTEGRATED INDEPENDENT REVIEW PENDING**. Publication, final-head CI and external
ChatGPT review remain pending. Overall A11a is incomplete; merge is not authorized.

## Initial contract checkpoint — historical

Original status: **CONTRACT ACCEPTED / IMPLEMENTATION AND VALIDATION PENDING**.
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

## Additive resumed checkpoint — 2026-10-04

This section supersedes the initial pending-implementation status while retaining
the accepted initial contract and historical agent/evidence record above.
The coordinator accepted **A11a.5-resume v1** before writes: reuse the paused
implementation for only offline `runs` and `trace`, with trace `--errors` / `--json`;
strict version-1 normalized metadata, exact IDs, bounded reads/JSON/output,
anchored no-follow private/single-link snapshots; no creation, cleanup, repair,
record mutation, config/credential/auth/provider/network/subprocess resolution,
raw/free-text/control rendering, or false completion from incomplete evidence.
Preserve ask stdout/exits, default-off persistence and provider-call counts.
No doctor, executor, hooks, router, retry, fallback, escalation, A11b/c or later
slice. Required evidence remains the declared focused/canonical/adversarial,
installed smoke/wheel provenance, docs/scope, independent review, exact final-head
Python 3.9/3.14 CI and draft-PR gates. External ChatGPT review remains separate
and pending; no merge or next slice is authorized. Max two correction rounds;
this is round 1. No requirement was removed or reduced during repair.

### Repository and execution provenance

- Configured Cloud checkout remains `/workspace/davidcattoolsproject`, origin
  `https://github.com/davidcatcat/davidcattoolsproject.git`; branch remains
  `feat/a11a5-runs-trace-cloud-20261004`. It was clean before switching.
- Initial base/merge base: `2bade6aa95963d3f2c3d9332aa3939407512da7e`.
  Reused implementation: `b02e8cf945a8201427ee1f0f2c158f83389e99c2`.
  Preserved paused checkpoint: `93fcb24e7501b6d45a74ea1d8c2c65c942a9cf5c`.
- Confirmed fetched latest main: `1cee64eaec4559b56c70dd6a5b508ca9f810160f`
  (architecture refinement PR #20 merge). The paused branch was 2 ahead / 5
  behind main; main's eight changed paths contained no runtime/test changes.
- Normal integration/corrected implementation commit:
  `94bbfe57193361d89711ac080e223296c7271701`, with parents exactly
  `93fcb24e7501b6d45a74ea1d8c2c65c942a9cf5c` and
  `1cee64eaec4559b56c70dd6a5b508ca9f810160f`. No rebase, rewrite, clone,
  worktree, alternate checkout or main-ref mutation occurred.
- Newest main's WORKFLOW, architecture spec appendix/validation, validate-runtime
  agent metadata and consolidated A11a.4 report are preserved byte-for-byte.
  README/ROADMAP current slice status and reader usage were reconciled.
- Resume Developer: `/root/a11a5_resume_developer`, requested `gpt-6.1-sol` / high;
  resume Reviewer: `/root/a11a5_resume_reviewer`, requested `gpt-6-astra` / high.
  Actual runtime model/reasoning for both remains **UNVERIFIED**; no tool
  attestation is available. Coordinator accepts evidence and is not the writer.
- Linux 6.18.44 x86_64; Python 3.12.14; pip 25.0.1; setuptools 84.0.0;
  wheel 0.48.0. Installed editable llmtool version 0.1.0 resolves to this checkout.
  Current Cloud observations: running/connected, HTTP unrestricted/enforced,
  no configured VPN, secrets, runtime variables or outbound identities. Proxy
  and CA trust remain inherited. No live inference/auth/Mac/VPN claim is made.

### Initial review findings and round-1 corrections

The independent initial source review on pinned `93fcb24` requested changes:

- **R1 / P2:** The reader inspected mutable artifact names/private-link state
  before acquiring the run lock. Real finish can expose `.summary-<id>` and
  two-link publication windows. The reader now validates the directory/existing
  lock and acquires a nonblocking shared run lock before scanning artifacts.
  Busy runs return `incomplete`, events `[]`, summary `null`; no mutable
  artifact names, stats or bytes are inspected. Stable snapshots retain all
  private/owner/regular/single-link/identity checks. Missing replacement run locks
  now classify as `STORE_UNSAFE` before artifact validation.
- **R2 / P2:** Summary-only listing previously rendered unqualified status.
  Human runs now explicitly says `Saved summaries only; trace evidence
  unverified. Use trace <run_id> to verify completion.` Rows use
  `recorded_status=<summary status>`. No reader schema or public flag was added.
  Runs remains bounded summaries-only; it never claims verified success or
  completion. Trace requires the summary and matching complete trace/end evidence.

New tests pause the actual writer at summary-temp existence, trace link-before-
unlink, and summary link-before-unlink, proving incomplete/no-artifact-stat/no-read,
zero mutation and unchanged bytes/modes/names/mtime/ctime/link counts. The writer
then completes successfully and trace reads complete. Forged valid success
summaries paired with empty/malformed/missing-end traces remain visibly recorded
and unverified in runs; trace fails safely with empty stdout. Missing trace with
summary is damaged; missing summary (with or without trace) remains incomplete.
No eager trace-body scan was added to runs.

An initial focused execution failed because the new multi-phase fixture retained
a prior completed run while forbidding every summary read. Each phase now uses
an isolated fixture. The existing replacement-directory test also now expects the
exact earlier `STORE_UNSAFE` missing-lock classification. Neither change weakens
product acceptance. The subsequent executions below passed.

### Fresh validation on corrected integrated bytes

All commands ran from the configured checkout. Production, tests and package
bytes were unchanged between execution and implementation commit `94bbfe571`;
`git diff 94bbfe571 --` on the runtime/test/package paths was empty afterward.
SHA256 of `git ls-tree -r 94bbfe571` for `tools/llmtool/llmtool`, `tests`,
`pyproject.toml`, `Makefile`, and `llmtool.py`:
`0490006bc405a1fc18f1f82ecc8233179d73e85397691088bfd2a5faa44ff158`.
Historical 784/814 results are not reused as resumed evidence.

| Check | Command / evidence | Result |
| --- | --- | --- |
| Public reader/adversarial/race suite | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_diagnostics_reader.py -v` | PASS, exit 0, 32/32, 1.221 s |
| Diagnostics and ask regressions | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_diagnostics*.py'` | PASS, exit 0, 209/209, 2.079 s |
| Canonical compileall/full unittest | `make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"` | PASS, exit 0, 816/816, no skips, 17.101 s |
| Offline wheel build | `tools/llmtool/.venv/bin/python -m pip wheel --no-deps --no-build-isolation --no-index --wheel-dir /tmp/a11a5-resume-wheel tools/llmtool` | PASS, exit 0 |
| Installed editable + wheel CLI | `tools/llmtool/.venv/bin/python /tmp/a11a5-resume-smoke.py` | PASS, exit 0; absent/saved/human/JSON/filter/invalid-ID/nonmutation |
| Wheel source/resource provenance | Byte comparison of all 42 packaged Python/skill files and console entry point | PASS |
| Main architecture/historical evidence preservation | Empty diff vs exact main on five protected documents/metadata paths | PASS |
| Docs/link/status/scope validation | 60 local Markdown link targets exist; current slice/gate wording and full diff checked; `git diff --check` | PASS, exit 0 |
| Integrated independent review | Exact committed documentation candidate to be frozen after this report | INCOMPLETE, pending |
| Final published Python 3.9/3.14 CI | Exact final documentation head | NOT-TESTED, unpublished |
| External ChatGPT source review | Separate required review gate | NOT-TESTED, pending |
| Draft PR delivery | Deferred until independent review converges | INCOMPLETE, pending |

The canonical run used the user's previously approved test-only `/var/tmp` empty
Codex fixture writes and loopback network permission. No credential discovery or
real provider requests occurred. Existing canonical ask tests prove output,
save/no-save/default-off, exit and exactly-one-provider-request regressions.
This offline slice has no required Mac/live-provider prerequisite; optional
Mac/Windows backend/live checks remain NOT-TESTED and are not implied by Linux tests.

Artifacts in this Cloud environment: `/tmp/a11a5-resume-diagnostics.log`,
`/tmp/a11a5-resume-canonical.log`, `/tmp/a11a5-resume-wheel.log`,
`/tmp/a11a5-resume-smoke.log`, `/tmp/a11a5-resume-smoke.py`, and
`/tmp/a11a5-resume-wheel/llmtool-0.1.0-py3-none-any.whl`. The reader result was
captured directly by the tool (32 tests, exit 0). Wheel SHA256:
`6e7824d0121b6371e2131b565fca08f7c9bb9a578cbbaffbe1de5aa18c666938`.
Smoke-script SHA256:
`4f0e88097a035b3122dbf17cb98d59139fa89b17d8ddb48d7887ee64833d2ec0`.
The wheel was installed into an isolated temporary package target (not a project
checkout), its import path verified, and both installed console scripts exercised
with isolated malformed config and temporary HOME. Saved-store snapshots were
identical before/after commands. No claim extends beyond tested cases.

After the documentation-only commit, the Developer freezes the exact clean HEAD
and stops all writes for independent review. The coordinator records that final
candidate SHA externally; the report does not attempt to embed its own commit SHA.
Publication/CI/external-review evidence must be bound to the eventual exact head.
Overall A11a is **NOT DONE**. A11a.6/7 and A11b/c remain unstarted; merge is blocked
and not authorized by this task.
