# Round 3 C3-1 — provider-response failure accounting validation (2026-10-06)

C3-1 remediation and offline checks PASS. Independent C3-B review remains pending;
**live Company Phase 0 is NOT STARTED and is not authorized by this report**.

## Contract, scope and revision

- Authoritative [remediation handoff](llmtool_token_savings_round3_live_wrapper_c3_1_remediation_handoff_20261006.md)
  and [coordinator review](llmtool_token_savings_round3_live_wrapper_coordinator_review_20261006.md).
- Canonical actual Mac repository:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- GitHub: `davidcatcat/davidcattoolsproject`; existing Draft PR #38.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`.
- Starting head, fetched and fast-forwarded safely:
  `22078e03d4c0cce683b63cbf9d4838fab741bc21`.
- Exact tested implementation head: `fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9`.
- Python: `/usr/bin/python3`, **Python 3.9.6**.

The user's expected remote head supersedes the earlier starting SHA in the handoff.
Only the experiment wrapper/test layer, its manifest and this fresh report changed.
No provider, runtime, skill, instruction, fixture, gold, or reviewed format helper
bytes changed. Existing unrelated untracked benchmark/reasoning/case artifacts were
preserved and not staged. The follow-up report publication is report-only and does
not change the tested implementation bytes.

## Before-fix reproduction

Permanent tests were added before changing `live_wrapper.py`. They invoke the real
CLI, real `OpenAICompatibleClient`, actual Request admission and runner accounting.
The instance opener returns an in-memory `BytesIO` response. Original urllib opener,
socket construction/connection, DNS and subprocess escape are blocked by the offline
guard. Both L02 and L03 are exercised.

Before fix, this focused command exited **1**:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py \
  OfflineTests.test_provider_invalid_json_terminal_accounting \
  OfflineTests.test_provider_invalid_finish_reason_terminal_accounting \
  OfflineTests.test_provider_invalid_message_terminal_accounting \
  OfflineTests.test_provider_response_too_large_terminal_accounting \
  OfflineTests.test_provider_length_terminal_accounting_still_distinct
```

Observed: **5 test methods, 14 failing subcases, 0 errors**. Invalid JSON, unexpected
finish reason, tool calls/function calls, empty/non-string content and too-large
response produced the correct production safe codes. The wrapper then rejected
those known codes with `WrapperIntegrityFailure` instead of terminalizing the
emitted trial. The length case retained its existing classification and passed.

The over-limit test uses a **test-local `RESPONSE_LIMIT=64` and 65 bytes**, exercising
the unchanged production read/length-check/error branch. It does not allocate
>16 MiB or change the production constant/source. The real 16-MiB allocation is
NOT-TESTED in this step; the production source still defines that limit.

## Minimal fix and permanent regression coverage

`failure_reason()` now additionally accepts exactly:

| Production safe code | Existing Phase-0 failure reason |
| --- | --- |
| `MODEL_RESPONSE_INVALID` | `TRANSPORT_OR_REQUEST_FAILURE` |
| `MODEL_RESPONSE_TOO_LARGE` | `TRANSPORT_OR_REQUEST_FAILURE` |

No failure category was added. `MODEL_OUTPUT_INCOMPLETE` remains distinct. Existing
network, timeout, runtime, HTTP, rate-limit and authentication mappings remain
unchanged; unknown codes still raise `WrapperIntegrityFailure`.

The existing `run_next()` and reviewed `Phase0Accounting` now receive the known
failure reason and complete the emitted trial using unavailable usage (`None`).
No repair, retry, increased max tokens, fallback or usage inference was added.

Seven new test methods bring the wrapper suite from **70 to 77**:

- `test_model_response_invalid_mapping`
- `test_model_response_too_large_mapping`
- `test_provider_invalid_json_terminal_accounting`
- `test_provider_invalid_finish_reason_terminal_accounting`
- `test_provider_invalid_message_terminal_accounting`
- `test_provider_response_too_large_terminal_accounting`
- `test_provider_length_terminal_accounting_still_distinct`

The existing length-runner test was strengthened without removing any assertion.
For every provider-boundary case, tests assert the actual safe code and:

```text
request_emitted = True
status = FAIL
primary_failure_reason = TRANSPORT_OR_REQUEST_FAILURE
usage_total = None
complete_total = None
```

For length, primary stays `MODEL_OUTPUT_INCOMPLETE` while both usage and complete
total remain unknown. Tests then run **every remaining logical trial through the
runner** and require `NOT_STARTED / COMPANY_TOKEN_ACCOUNTING_UNKNOWN`, no usage
value, no request emission and no second delegation. Fake opener count remains
exactly one, `max_tokens` remains 4096, and no raw-success artifact is created for
these failed responses. Production response failures never reach semantic adapters.

## Frozen manifest and Request identity

The manifest was regenerated using the real CLI/provider zero-network freeze path.
Before writing it, the entire old and new `lanes` objects were compared and required
to be identical; all other manifest values except the artifact hash map also had
to match. Exactly two artifact hashes changed: wrapper and wrapper tests.

| Artifact | SHA-256 |
| --- | --- |
| Old preregistration | `81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece` |
| New preregistration | `0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276` |
| New `live_wrapper.py` | `3429c09cc56c5be7e973c1d71b35ce6f2a027dfe0dbecb3e7cc6ab1e18f70360` |
| New `test_live_wrapper.py` | `a666e0d45648a9d0836af0556eaa64e10e3905b4e9ad05d4282f69e0f5177378` |
| L02 Request body, unchanged | `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f` |
| L03 Request body, unchanged | `6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0` |
| L02 user message, unchanged | `e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac` |
| L03 user message, unchanged | `3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2` |

Instruction, fixture, skill, config, grading-metadata and production source hashes
are unchanged. After committing, the new manifest was loaded through
`load_preregistration(expected_sha256)`, and real CLI dry runs repeated. L02 and L03
each captured **one** real constructed Request, with **zero network calls and zero
socket opens**. Freeze facts matched both starting-head and new manifests exactly.

The endpoint `home-llm.example:8000` was **not accessed**. These are offline Request
construction/fake-response tests, not successful Company inference attempts.

## Exact-head Python 3.9 results

Re-executed on `fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9`:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py
git diff --check
```

| Check | PASS | FAIL | ERROR | SKIP | Exit | Suite seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| format_probe | 88 / 88 | 0 | 0 | 0 | 0 | 0.116 |
| live_wrapper | 77 / 77 | 0 | 0 | 0 | 0 | 4.802 |
| `git diff --check` | PASS | 0 | 0 | 0 | 0 | n/a |

Raw local test logs are temporary validation evidence; no full trace or fabricated
live results are committed. Ordinary diff review found only the two-code allowlist
addition, new/strengthened tests and two updated artifact digests. Diff against the
starting head for production/runtime/skills, instructions, Round-2 fixtures/gold,
`format_probe.py` and `test_format_probe.py` is empty.

## Remaining gates and stop point

Independent C3-B review must pin the **new** manifest hash and wrapper bytes before
any future Company live authorization. The old manifest hash is historical evidence
and must not authorize the remediated wrapper. No live entry path or authorization
mechanism was expanded in C3-1. Live inference/format compliance and Codex benchmark
are NOT-TESTED and forbidden in this step. No merge or next phase was performed.

```text
Exact tested head: fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9
Python: 3.9.6
format_probe: 88 / 88 PASS
live_wrapper: 77 / 77 PASS
FAIL: 0
ERROR: 0
SKIP: 0
Exit: 0
git diff --check: PASS
C3-1 MODEL_RESPONSE_INVALID: PASS
C3-1 MODEL_RESPONSE_TOO_LARGE: PASS
MODEL_OUTPUT_INCOMPLETE classification: UNCHANGED / PASS
Emitted failure accounting: PASS
Unknown usage global stop: PASS
Zero-network freeze: PASS
Network calls: 0 (provider HTTP)
Socket opens: 0 (offline execution)
Old preregistration SHA: 81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece
New preregistration SHA: 0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276
L02 request_body SHA: cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f
L03 request_body SHA: 6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0
Request/body hashes changed: NO
Company live calls: 0
Codex calls: 0
Production/runtime/skill changes: NONE
Ready for independent C3-B review: YES
Live Company Phase 0: NOT STARTED
```
