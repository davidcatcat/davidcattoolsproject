# EC2 remote-test receipt limits (issue #2281 row 9) — fact record

Read scope for this note: `tools/llmtool/scripts/remote_test_receipt.py` and `tools/llmtool/policy.json`. Everything else is outside the read scope.

This note records only current facts about the receipt script. It contains no options, recommendations or plans.

## Numeric limits defined by the receipt script

All six limits are module-level constants with hard-coded numeric literals in `tools/llmtool/scripts/remote_test_receipt.py`. No value is loaded from any file. Every "where it is applied" entry below is inside `tools/llmtool/scripts/remote_test_receipt.py` and names the function that applies the constant.

| Constant | Current value | What it bounds | Where it is applied |
| --- | --- | --- | --- |
| `_MODULE_TIMEOUT` | 180 | Per-module wall time for one unittest module run | `main()` computes `min(_MODULE_TIMEOUT, remaining)` for each module and passes it to the module runner; `run_module()` uses it as the `subprocess` communicate timeout via `max(0.1, float(timeout))` and kills the process group on expiry |
| `_TOTAL_TIMEOUT` | 540 | Total wall time for the whole receipt run | `main()` sets `deadline = begin + _TOTAL_TIMEOUT`; the remaining time caps each module via `min(_MODULE_TIMEOUT, remaining)`, and if `remaining <= 0` before a module starts, the run is recorded as `TIMEOUT` with exit code 124 and the module recorded as `NO_FINAL` without running |
| `_REMOTE_LIMIT` | 32768 | Size in bytes of the serialized receipt | `_write_receipt()` raises `ValueError("receipt exceeds limit")` when the encoded receipt bytes exceed it; `main()` catches that and returns exit code 1, so no `receipt.json` is written in that case |
| `_FAILURE_EXCERPT_LIMIT` | 4096 | Byte size (UTF-8 encoded, including newlines) of the collected failure excerpt | `failure_excerpt()` stops collecting lines when adding the next encoded line would exceed it; the excerpt is stored in the receipt `failure_excerpt` field by `main()` when the result is not `PASS` |
| `_FAILURE_EXCERPT_LINES` | 40 | Number of failure-excerpt lines | `failure_excerpt()` stops collecting when 40 lines are already kept |
| `_MAX_LOG_BYTES` | 20_000_000 (20000000) | Value of the `log_bytes` receipt field for the combined stdout+stderr bytes of all modules | `main()` writes `min(len(log), _MAX_LOG_BYTES)` as `log_bytes` in the receipt dict |

Two further numeric bounds in `tools/llmtool/scripts/remote_test_receipt.py` are inline literals in `main()`, not module-level constants: the per-module `final` line is truncated to 512 characters when building `finals`, and the receipt `failures` list is truncated to the first 10 entries.

## Does the script read tools/llmtool/policy.json?

No. `tools/llmtool/scripts/remote_test_receipt.py` never references `policy.json`: its only `json` usage is `json.dumps` inside `_write_receipt()` (serializing the receipt), and the only file it writes is `receipt.json` in the out directory. The six constants above are assigned numeric literals at module level and are never read from `tools/llmtool/policy.json` or any other file.

Facts about `tools/llmtool/policy.json` itself: it is a single JSON object with `"schema_version": 1` and the keys `repair_max`, `parent_debug_max`, `group_debug_max`, `child_max`, `replan_max`, `test_fail_max`, `review_fail_max`, `waiting_notify_after_hours`, `timeout_continuation_max`, `spec_preflight_large_file_lines`, `merge_train_members_max`, `merge_train_files_per_pr_max`, `revert_scope_prs_max`, `d6_writer_lease_hours`. None of these keys corresponds to the receipt script's six constants above. Any other code that might read or write these files is outside the read scope.
