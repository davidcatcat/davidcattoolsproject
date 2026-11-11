# llmtool Round 3 — exact live Company Phase-0 execution handoff (prepared 2026-10-06)

## Status

**FINAL-REVIEW DRIVER HARDENED — NOT YET EXECUTED LIVE.**

This document defines the exact first real Company-only Round-3 Phase-0 run.

Do not send any Company request merely by reading/preparing this handoff.
One final review of this handoff is required before execution authorization.

## Reviewed implementation

Repository:
`davidcatcat/davidcattoolsproject`

Canonical Mac repo only:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Exact tested implementation head:
`3325f75e954a4099e31c5936412307320974cb4f`

Independent C3-B/B1 targeted re-review: PASS.

## Hard-coded reviewed manifest authorization

The live runner MUST use this literal SHA-256:

`37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`

Do not:
- compute the authorization pin from the current manifest file;
- substitute a newer pin;
- use historical pins;
- accept a registration dict;
- regenerate preregistration before the run.

Historical pins are NOT authorized:
- `0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276`
- `81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece`

The only authorized constructor is:

```python
runner = Phase0Wrapper(
    expected_manifest_sha256=
    "37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404"
)
```

## Frozen Request identity

L02:
- user_message_sha256:
  `e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac`
- request_body_sha256:
  `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`

L03:
- user_message_sha256:
  `3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2`
- request_body_sha256:
  `6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0`

Any drift is a hard STOP before network.

## Provider contract

Endpoint:
`http://192.0.2.10:4000/v1/chat/completions`

Model route:
`gw`

Expected observed model family may be company-side qwen, but no model-name inference is
used for admission.

Frozen generation:
- temperature = 0
- max_tokens = 4096
- enable_thinking = false

No Authorization header.
No retry.
No fallback.
No stream.
No tool call.
No max-token increase.

## Operator/VPN precondition

Before the authorized live command is run, the operator must confirm the Company VPN is
already connected.

Do NOT perform a direct curl, smoke inference, health inference, or throwaway Company
model request before Phase 0. The first model request must be the frozen L02-1 trial.

A VPN/transport failure after Request emission is a real consumed/possibly-consumed
trial under the frozen accounting rules. It is not retried.

## Canonical-repo preflight

Run from:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Required preflight:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject

test "$(pwd -P)" = "/Users/davidsun/git/davidcataiporject/davidcattoolsproject"

git fetch origin

test "$(git branch --show-current)" = "bench/llmtool-token-savings-round3-format-compliance-20261006"

test "$(git rev-parse HEAD)" = "$(git rev-parse origin/bench/llmtool-token-savings-round3-format-compliance-20261006)"

test -z "$(git status --porcelain --untracked-files=no)"

git diff --exit-code   3325f75e954a4099e31c5936412307320974cb4f..HEAD   -- reports/token_benchmark/20261006_round3

test -z "$(git diff --name-only   fecbdbae928b63de2661ed4b8faf6dbfff964400..HEAD   | grep -v '^reports/' || true)"

test "$(shasum -a 256   reports/token_benchmark/20261006_round3/phase0_preregistration.json   | awk '{print $1}')" = "37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404"
```

If ANY check fails: STOP. Do not repair, reset, regenerate, or continue automatically.

Unrelated untracked user files may remain untouched. Never clean/reset/stash them.

Never use `/private/tmp/*` worktrees/clones.

## Final offline preflight

Immediately before live authorization/execution, rerun:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py

/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py

git diff --check
```

Required:
- helper 88/88 PASS;
- wrapper 94/94 PASS;
- 0 FAIL;
- 0 ERROR;
- 0 SKIP;
- git diff --check PASS.

Then verify the literal pin through the sealed loader without building a new manifest:

```sh
/usr/bin/python3 - <<'PY'
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "reports/token_benchmark/20261006_round3"))
import live_wrapper as w

PIN = "37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404"
r = w.load_preregistration(PIN)
assert r["order"] == ["L02-1","L03-1","L02-2","L03-2","L02-3","L03-3"]
assert r["retry"] is False
assert r["max_requests_per_trial"] == 1
assert r["ceiling"] == 300000
assert r["upper_bounds"] == {"L02": 35000, "L03": 59000}
print("PINNED_MANIFEST_OK")
PY
```

This is offline manifest verification only. It sends no Company request.

## Private live raw-output directory

The exact first-run raw directory is:

`/Users/davidsun/.llmtool-round3-phase0-live/37f48c17-run1`

It is outside the repository and is not a Git worktree/clone.

Before execution:

```sh
RAW_PARENT=/Users/davidsun/.llmtool-round3-phase0-live
RAW_DIR="$RAW_PARENT/37f48c17-run1"

mkdir -p "$RAW_PARENT"
chmod 700 "$RAW_PARENT"

test ! -e "$RAW_DIR"
mkdir -m 700 "$RAW_DIR"
```

If `RAW_DIR` already exists: STOP. Do not delete, overwrite, rename, or reuse it.

Unscanned Company stdout must remain outside the tracked repository.

## Exact live logical order

The frozen logical order is:

1. L02-1
2. L03-1
3. L02-2
4. L03-2
5. L02-3
6. L03-3

Maximum physical Company requests: 6.

Actual requests may be fewer because:
- semantic/format failure stops later trials for that fixture;
- unknown consumed usage stops all later trials;
- budget admission can stop a trial before request;
- wrapper integrity failure stops execution immediately.

Never replace a skipped trial with another request.

## Token admission

Complete Phase-0 ceiling:
`300000`

Pre-request bounds:
- L02 = 35000
- L03 = 59000

Provider usage is accepted only if prompt/completion/total are exact nonnegative ints and:

`total_tokens == prompt_tokens + completion_tokens`

Missing/inconsistent usage after an emitted request means:
- usage unknown, never zero;
- complete token accounting unknown;
- all later logical trials NOT_STARTED /
  COMPANY_TOKEN_ACCOUNTING_UNKNOWN.

## Failure semantics

Provider/request failures:
- MODEL_RESPONSE_INVALID -> TRANSPORT_OR_REQUEST_FAILURE
- MODEL_RESPONSE_TOO_LARGE -> TRANSPORT_OR_REQUEST_FAILURE
- NETWORK_PROVIDER_UNREACHABLE -> TRANSPORT_OR_REQUEST_FAILURE
- PROVIDER_TIMEOUT -> TRANSPORT_OR_REQUEST_FAILURE
- PROVIDER_RUNTIME_ERROR -> TRANSPORT_OR_REQUEST_FAILURE
- PROVIDER_HTTP_ERROR -> TRANSPORT_OR_REQUEST_FAILURE
- PROVIDER_RATE_LIMITED -> TRANSPORT_OR_REQUEST_FAILURE
- AUTH_PROVIDER_REJECTED -> TRANSPORT_OR_REQUEST_FAILURE

MODEL_OUTPUT_INCOMPLETE remains:
`MODEL_OUTPUT_INCOMPLETE`

No retry for any failure.

## Exact live execution driver

After final review explicitly authorizes live execution, run exactly one serial process
from canonical repo.

Do not create a permanent runner script in the repo.

Use:

```sh
/usr/bin/python3 - <<'PY'
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path("/Users/davidsun/git/davidcataiporject/davidcattoolsproject")
assert Path.cwd().resolve() == ROOT

sys.path.insert(0, str(ROOT / "reports/token_benchmark/20261006_round3"))
import live_wrapper as w

PIN = "37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404"
OUT = Path("/Users/davidsun/.llmtool-round3-phase0-live/37f48c17-run1")
STATE = OUT / "phase0_state.json"

assert OUT.is_dir()
assert not STATE.exists()

runner = w.Phase0Wrapper(expected_manifest_sha256=PIN)

ORDER = ("L02-1","L03-1","L02-2","L03-2","L02-3","L03-3")

def snapshot(run_status, error_type=None, next_trial=None):
    records = [asdict(r) for r in runner.accounting.records]
    payload = {
        "run_status": run_status,
        "error_type": error_type,
        "next_trial": next_trial,
        "pinned_manifest_sha256": PIN,
        "gate_passed": runner.accounting.gate_passed,
        "known_cumulative": runner.accounting.known_cumulative,
        "complete_total": runner.accounting.complete_total,
        "halted": runner.halted,
        "records": records,
    }
    tmp = OUT / (".phase0_state.%d.tmp" % os.getpid())
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Atomic replacement: never unlink the previous durable state first.
        os.replace(tmp, STATE)
    finally:
        if tmp.exists():
            tmp.unlink()
    return payload

try:
    snapshot("READY", next_trial=ORDER[0])

    for index, expected in enumerate(ORDER):
        # Durable intent record before a possibly consuming Company request.
        snapshot("BEFORE_TRIAL", next_trial=expected)

        record = runner.run_next(OUT)
        assert record.trial_id == expected

        following = ORDER[index + 1] if index + 1 < len(ORDER) else None
        snapshot("RUNNING", next_trial=following)

    final = snapshot("COMPLETED", next_trial=None)
    print(json.dumps(final, indent=2, sort_keys=True))
except BaseException as exc:
    emitted_started = any(
        r.request_emitted and r.status == "STARTED"
        for r in runner.accounting.records
    )
    status = (
        "ACCOUNTING_INCOMPLETE_UNKNOWN"
        if emitted_started
        else "INTEGRITY_OR_EXECUTION_HALT"
    )

    try:
        final = snapshot(status, type(exc).__name__)
        print(json.dumps(final, indent=2, sort_keys=True))
    except BaseException as snapshot_exc:
        # Keep the previous durable STATE intact; do not mask the original error.
        print(json.dumps({
            "run_status": "STATE_SNAPSHOT_FAILURE",
            "original_error_type": type(exc).__name__,
            "snapshot_error_type": type(snapshot_exc).__name__,
            "pinned_manifest_sha256": PIN,
        }, sort_keys=True), file=sys.stderr)

    raise
PY
```

No other Company/provider command may run in parallel.

## STOP conditions

Stop immediately and do not retry if any of these occurs:

- manifest SHA mismatch;
- implementation/artifact/instruction drift;
- Request body/user-message digest mismatch;
- WrapperIntegrityFailure;
- unexpected safe error code;
- output secret/publication guard failure;
- raw-output replay mismatch;
- grading metadata digest mismatch;
- request emitted but accounting cannot be completed;
- process interruption after request emission;
- any need to change prompt/config/max_tokens/retry behavior.

If a Request was emitted and a later integrity/execution halt leaves the logical trial
STARTED:

Report exactly:
`ACCOUNTING INCOMPLETE / UNKNOWN`

Do not report `known_cumulative` as the complete token total.

Do not resume this run.
Do not create run2 without a new explicit decision.

### Hard process interruption / power-loss rule

The driver writes a durable `BEFORE_TRIAL` state **before every potentially consuming
trial** and atomically replaces the prior state without unlinking it first.

If the Python process disappears without reaching its exception handler (for example
SIGKILL, terminal loss with forced kill, OS crash, or power loss) and the private raw
directory exists:

- do not rerun or resume;
- inspect the last `phase0_state.json` only;
- if its `run_status` is `BEFORE_TRIAL`, conservatively treat that named
  `next_trial` as possibly emitted/consumed;
- classify the run as `ACCOUNTING INCOMPLETE / UNKNOWN`;
- do not use `known_cumulative` as a complete total;
- do not create run2 without a new explicit coordinator decision.

This conservative rule applies even if no `*.stdout` exists for the interrupted trial.

## Normal semantic/format failure is not a manual retry condition

A normal terminal FAIL returned by `run_next()` is handled by the frozen state machine.

Continue the six logical slots in the same process so that:
- the failed fixture's later slots become NOT_STARTED;
- the other fixture may still receive its diagnostic trials;
- unknown token accounting globally stops later requests.

This continuation is state-machine bookkeeping, not a retry.

## Phase-0 PASS condition

Phase 0 passes only if after six logical slots:

- exactly six TrialRecords exist;
- all six status == PASS;
- all six request_emitted == true;
- complete_total is an integer <= 300000;
- gate_passed == true.

Equivalent requirement:
- L02 passes 3/3;
- L03 passes 3/3 including post-freeze target recall 7/7;
- all provider usage is complete and valid.

Anything else means:
`PHASE0_GATE_PASS = NO`

No expensive Codex paired benchmark may start from a failed/unknown Phase 0.

## Publication/reporting after the live process

Never commit raw `*.stdout` files directly from the private raw directory.

After execution, inspect only:
- `phase0_state.json`;
- TrialRecord fields;
- request/evaluation/raw SHA values;
- usage totals;
- failure reasons/diagnostics.

Do not paste raw Company output into the report.

Create a sanitized report:

`reports/llmtool_token_savings_round3_phase0_live_results_20261006.md`

It must record:
- exact branch HEAD used;
- exact tested implementation head;
- literal manifest pin;
- VPN manually confirmed before run: YES;
- raw directory path (outside repo);
- six logical TrialRecords in order;
- request_emitted per slot;
- status per slot;
- usage_total per slot;
- primary/secondary failure reason;
- request_body/evaluation/raw stdout SHA fields when available;
- known_cumulative;
- complete_total (or UNKNOWN);
- gate_passed;
- physical request count;
- retry count = 0;
- Company live calls = physical request count;
- Codex benchmark calls = 0;
- whether any integrity halt occurred;
- whether accounting is COMPLETE or INCOMPLETE/UNKNOWN;
- Phase-0 final verdict PASS / FAIL / INDETERMINATE.

Do not include raw response text or credential-like values.

## Decision after Phase 0

If PASS:
- STOP after publishing the sanitized report;
- do not immediately start Codex benchmark;
- coordinator reviews the Phase-0 report first;
- then decide whether to preregister the expensive paired Round-3 benchmark.

If FAIL:
- STOP;
- no retries;
- coordinator diagnoses the frozen failure;
- do not edit instructions and rerun under the same preregistration.

If INDETERMINATE / ACCOUNTING UNKNOWN:
- STOP;
- do not use partial totals as complete economics;
- no paired benchmark.

## Final-review checklist for this handoff

Before authorizing live execution, reviewer must confirm:

- literal pin is exactly
  `37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`;
- no pin is computed at runtime from manifest;
- no direct Company smoke/curl precedes L02-1;
- raw output path is outside repo;
- execution uses one serial runner instance;
- logical order is frozen six slots;
- no retry/fallback;
- state file contains no raw model text;
- all STOP conditions are fail-closed;
- PASS requires all six PASS and complete accounting;
- raw stdout is never committed;
- no Codex benchmark starts automatically.

Only after this document passes final review may the user explicitly authorize the first
live Company Phase-0 run.
