# llmtool P1 — safe usage metadata on provider failures

Validation performed on the canonical Mac on 2026-10-07 (Asia/Taipei).
This is a production observability/accounting improvement, not a benchmark run.

## Revision and task contract

- Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`
- Remote identity: `davidcatcat/davidcattoolsproject`
- Base main SHA, freshly fetched before branching: `59ef2b7faca481a6e40f6d535960032547ce1c6a`
- Branch: `feat/llmtool-p1-failure-usage-metadata-20261006`
- Tested implementation SHA: `eac81a32a797ef2c372b58757e7625206ed2649c`
- Full suites ran on the implementation working tree immediately before commit;
  the committed source/test/README bytes are identical. The Python 3.9 focused
  suite was also rerun at the implementation SHA. This report is a later docs-only commit.
- Acceptance: retain only a validated complete usage triplet on safely decoded
  failure envelopes, without changing primary failure classification or allowing
  response text into error/diagnostic metadata.
- No Round-3 report/result/manifest/wrapper changes, Round 4, retry, fallback,
  production routing, or merge are included.

## Changed files

Production:

1. `tools/llmtool/llmtool/diagnostics.py` — shared bounded usage validation.
2. `tools/llmtool/llmtool/errors.py` — optional read-only typed-error metadata.
3. `tools/llmtool/llmtool/providers/openai_compatible.py` — extract validated usage
   before checking parsed response validity and completeness.
4. `tools/llmtool/llmtool/diagnostics_runtime.py` — observe safe failed-response
   usage through the existing provider.response event path.

Tests: `test_provider.py`, `test_diagnostics.py`, `test_diagnostics_runtime.py`,
and `test_diagnostics_ask_persistence.py`, all under `tools/llmtool/tests/`.
Sixteen permanent test methods were added; the existing success test additionally
asserts the whole triplet and metadata order. No existing assertion was weakened.

Documentation: a minimal paragraph in `tools/llmtool/README.md` and this report.
Runtime skills and other production files are unchanged.

## Safe metadata contract

`LLMToolError.provider_metadata` is `None` or a detached immutable mapping with
exactly `prompt_tokens`, `completion_tokens`, and `total_tokens`. The constructor
and optional `coded_error(..., provider_metadata=...)` keyword validate it using
`diagnostics.validate_provider_usage()`. Public property reassignment and mapping
item assignment are rejected.

The validator reuses the diagnostics integer and metadata byte bounds. Counts
must be exact integers, excluding bool and other types, in `[0, MAX_TOKEN_COUNT]`
where `MAX_TOKEN_COUNT = 10**12`. All three fields are required and total must
equal prompt plus completion. Partial, inconsistent, unknown-field, or otherwise
invalid metadata is dropped. No absent value is estimated or replaced by zero;
an explicitly reported valid zero triplet is accepted.

The provider first requires successful bounded JSON decoding and a dict envelope.
It selects only the three usage fields before validation: extra upstream usage
fields are ignored, never attached or rendered. Direct typed-error metadata with
extra fields is rejected. The successful Reply metadata path keeps its previous
individual-field acceptance and ordering; this change does not tighten successful
response semantics.

Metadata validation/construction failure is secondary and cannot replace the
original error. `str(exc)`, `repr(exc)`, `args`, and the closed DiagnosticError JSON
do not gain token fields. Legacy uncoded pickle/deepcopy still work. Coded errors
remain unsupported for pickle/deepcopy, as before; the diagnostic JSON schema is
unchanged. In-process callers can deterministically inspect the optional property.

For ask failures with valid usage, the existing observer emits provider.response
before error and run.end. The event includes the numeric triplet and existing
`token_usage_available=true`. Verbose/debug display follows the existing metadata
path. Persistence remains default-off and uses only the existing explicit opt-in.
Incomplete/error summaries, final error codes, exit 1, quiet normal stdout, and
single provider invocation remain unchanged.

## Before-fix evidence

Before production edits on the fetched base, the new provider suite ran 30 tests
and reported 32 errors from absent `provider_metadata` access across subtests.
A narrower deterministic reproducer then asserted the required contract:

```sh
PYTHONPATH=tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest \
  test_provider.ProviderTests.test_failure_usage_length_preserves_triplet_and_original_error
```

Python 3.13.3: 1 test, FAIL 1, exit 1. A fake in-memory response reported
`100 + 4096 = 4196` with finish_reason=length. Actual error metadata was `None`;
expected metadata was the complete triplet. The original MODEL_OUTPUT_INCOMPLETE
code/message already existed. After the fix this same test passes.

## Behavior matrix and permanent regression coverage

| Case | Result | Principal test |
|---|---|---|
| Success + valid usage | PASS: original Reply content, triplet and order | `test_success_only_final_content_and_safe_metadata` |
| Length + valid usage | PASS: MODEL_OUTPUT_INCOMPLETE with triplet | `test_failure_usage_length_preserves_triplet_and_original_error` |
| Length + missing/partial/bool/negative/string/overflow/inconsistent usage | PASS: same primary error, metadata unavailable | `test_failure_usage_missing_partial_invalid_remains_unavailable` |
| Unexpected finish_reason, invalid choices/message, tool_calls/function_call, empty or invalid-Unicode content, parsed API error + valid usage | PASS: MODEL_RESPONSE_INVALID with triplet | `test_failure_usage_parseable_invalid_responses_preserve_triplet` |
| Invalid JSON or non-dict envelope | PASS: metadata unavailable | `test_failure_usage_invalid_json_and_nondict_have_no_metadata` |
| Oversized response | PASS: no JSON decode and no usage | `test_failure_usage_oversize_body_is_never_decoded` |
| HTTP, timeout, network, I/O failure | PASS: original codes/retryability, no usage | `test_failure_usage_http_network_timeout_do_not_parse_body` |
| Metadata validation failure | PASS: primary error preserved | `test_failure_usage_validation_failure_preserves_primary_error` |
| Raw body/key/model/reasoning/prompt contamination | PASS: closed numeric-only metadata | `test_failure_usage_cannot_carry_raw_content_or_credentials` |
| Detachment, immutability, strict numeric bounds/keys, diagnostic construction and transfer compatibility | PASS | five new `ErrorCompatibilityTests` methods |
| Normal/verbose/debug failure observation and secondary observer fault | PASS: exit/code/status preserved | two new `AskTests` methods |
| Saved incomplete/invalid response | PASS: safe usage event, durable failed summary | `test_saved_provider_failure_retains_only_safe_usage` |

MODEL_OUTPUT_INCOMPLETE + valid usage: **PASS**.
MODEL_OUTPUT_INCOMPLETE + invalid/missing usage: **PASS**.
MODEL_RESPONSE_INVALID + valid usage: **PASS**.
Invalid JSON / too-large / transport, usage unavailable: **PASS**.
Original error code preserved: **PASS**.
Original error message preserved: **PASS**.

## Commands and results

Focused suites cover the new methods and existing provider, diagnostics foundation,
runtime, metadata regressions, failure matrix, and ask persistence tests:

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest \
  test_provider test_diagnostics test_diagnostics_runtime \
  test_diagnostics_metadata_regressions test_diagnostics_failure_matrix \
  test_diagnostics_ask_persistence

PYTHONPATH=tools/llmtool:tools/llmtool/tests /usr/bin/python3 -B -m unittest \
  test_provider test_diagnostics test_diagnostics_runtime \
  test_diagnostics_metadata_regressions test_diagnostics_failure_matrix \
  test_diagnostics_ask_persistence

make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

| Check | Python | Run | PASS | FAIL | ERROR | SKIP | Exit |
|---|---|---:|---:|---:|---:|---:|---:|
| Focused | 3.13.3 | 181 | 181 | 0 | 0 | 0 | 0 |
| Focused, including committed-head rerun | 3.9.6 | 181 | 181 | 0 | 0 | 0 | 0 |
| Canonical syntax + full suite | 3.13.3 | 1037 | 1036 | 0 | 0 | 1 | 0 |
| Canonical syntax + full suite | 3.9.6 | 1037 | 1036 | 0 | 0 | 1 | 0 |

Full suite times: 163.795 seconds (3.13.3), 295.078 seconds (3.9.6).
The sole skip is `test_unusual_filename_bytes_roundtrip`: the Mac host filesystem
rejects non-UTF-8 filename bytes. Python 3.14 is **NOT TESTED LOCALLY**; final-head
GitHub Actions has the existing 3.9/3.14 matrix.

The initial sandbox full-suite attempt failed on existing process-state and
loopback permission restrictions (991 tests counted, 60 errors, 1 skip; fixture
setup failures prevent normal test execution/counting). `/bin/ps` explicitly
returned operation not permitted. The same canonical check then passed outside
the sandbox; no production/test change bypassed those checks. An initial focused
3.9 invocation omitted the project PYTHONPATH and failed module imports; correcting
the command produced the 181-test PASS above, without a source change.

Installed checkout CLI `--version` (`llmtool 0.1.0`) and `--help`: exit 0.
`git diff --check`: **PASS**. All nine pre-existing untracked artifact paths were
preserved; no task runtime artifacts or modified user config were added to Git.

## Focused security and scope review

Developer source/diff review used the review-change and security-review workflows;
this is not an independent reviewer verdict. Protected assets are prompt/response
text, credentials, provider raw bodies, and existing diagnostic privacy boundaries.
Untrusted provider JSON crosses into typed error metadata only via exact allowlisted
integer selection, validation and detached freezing. Neither this feature nor
usage counts grant execution/retry/fallback authority.

Synthetic-marker tests check exception text/repr/diagnostic JSON and persisted
events for prompt, API key, Authorization, model text, raw body and reasoning.
Secret/raw response leakage observed: **NONE**. Existing JSON/response bounds,
provider request safety, timeout, persistence/store security, process lifecycle,
inherited signal semantics, and failed-task classification remain covered by the
full suite. No material issue was found in this focused developer review.

Company live calls: **0**.
Codex benchmark calls: **0**.
Only synthetic in-memory/loopback provider responses and fake CLI processes were
used. Real upstream usage accuracy is **NOT TESTED**. Other callers, including
frozen benchmark wrappers, have not been migrated to consume the new property;
historical unknown usage remains unknown. No historical benchmark verdict changes.

## Publication and remaining gate

Implementation and this report are published on the P1 feature branch for a Draft
PR. Final report-commit HEAD and exact-head CI are recorded in the PR/checks and
publication response; they are not represented by this report's implementation SHA.
Independent review is pending. Do not merge or begin Round 4 from these results.

Ready for independent review: **YES**, subject to exact-head CI confirmation.
