# Company gw deployment probe

Run this only from the PM's authorized host after confirming the `company-gw` profile is configured. The probe sends synthetic prompts to the configured gateway. It does not read repository source or internal names. The API credential is read through llmtool configuration and kept in memory for request authorization; it is never intentionally written to output or printed.

```sh
tools/llmtool/.venv/bin/python -I -B tools/llmtool/eval/gw_probe/probe.py --out-dir /private/tmp/gw-probe
```

Use `--only NAME` to run one probe, or repeat it for a selection. Names are `models`, `thinking`, `tools`, `determinism`, `context`, `output`, `concurrency`, and `quality`. Results go to `gw_probe.json` and `gw_probe.md` under `--out-dir`; without it, they go under the system temporary directory in `gw-probe`.

`--dry-run` uses built-in canned responses and makes no network requests. `--base-url` and `--model` are intended for tests with a loopback fake server; endpoint and model overrides require a loopback base URL (or `--dry-run`). Calls have a 30-second timeout by default; context and output calls use 300 seconds, and thinking-on quality calls use 120 seconds. `TIMEOUT` means the client-side request limit was reached; it does not report a gateway timeout limit. Response bodies are bounded.

The probes measure:

- `models`: model IDs and reported context length fields.
- `thinking`: the same small arithmetic prompt with thinking off, on, and unset, each at low and high reasoning effort; captures status, reasoning length, think tags, usage, latency, and answer.
- `tools`: whether a one-function `tool_choice: auto` request returns tool calls.
- `determinism`: four identical zero-temperature prompts and answer variation.
- `context`: a needle at the beginning and question at the end of increasingly long prompts, from about 8K up to 300K tokens; stops at the first request error.
- `output`: an open-ended count request with 4K, 16K, and 32K max token limits.
- `concurrency`: batches of 1, 4, 8, 16, and 32 small requests, with success/error counts, wall time, and aggregate output tokens per second.
- `quality`: 20 synthetic arithmetic, logic, and code-reading questions with thinking off and on, both with a 2048-token output budget, including each answer, finish reason, completion-token count, accuracy, latency, and token usage.

An HTTP or transport error is represented in that probe's result and does not stop later probes. The reported context size is approximate; gateway-reported token usage is recorded separately when available. This is a bounded deployment measurement, not a general model evaluation.

## Multi-turn tools probe

`multiturn.py` runs six synthetic scenarios: native tool round-trip (S1), forced tool selection (S2), matching `tool_call_id` round-trip (S3), streamed tool calls (S4), thinking with tools (S5), and a text-tools redo of S1 (S6). It creates a small synthetic project in a mode-0700 temporary directory and does not read repository source.

Loopback fake-server runs need no live authorization:

```sh
python -B tools/llmtool/eval/gw_probe/multiturn.py --base-url http://127.0.0.1:8123 --model gw --expected-served-model fake-model --out-dir /private/tmp/gw-probe-multiturn
```

Configured non-loopback runs require both `--live` and `--owner-authorization-url` with the PM-relayed Issue comment URL that records Owner authorization. The existing `company-gw` user configuration is read without printing it. Requests use the configured model (`gw`); served responses must report `qwen3.8-27b` unless `--expected-served-model` overrides it. Calls run serially, with a 60-minute run limit and 300-second per-call timeout. There is no call or token limit by default; `--max-calls N` and `--max-total-tokens N` set optional limits. The run stops on authorization failures, two consecutive connection errors, served-model mismatch, or credential-scan hits. There are no retries or fallback requests. `audit.jsonl` is a hash-chained record of sanitized event metadata; it omits prompts, response bodies, credentials, headers, and endpoint values. Results and audit verification are written under the mode-0700 output directory.

Offline fake-server coverage is in `test_multiturn.py`; input-screening helper coverage is in `test_gw_probe.py`.
