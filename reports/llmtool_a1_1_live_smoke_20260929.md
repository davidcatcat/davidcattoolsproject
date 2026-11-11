# A1.1 Live Smoke Test

**Date:** 2026-09-29
**Branch:** `feat/a1-configure-wizard`
**Code commit tested:** `637d88ed017d6866c65ee1c24bf20a0dd47b1c28`
**Result:** PASS, with a generated-command safety observation noted below.

## Environment and setup

- Created `tools/llmtool/.venv` and installed the package in editable mode with `pip install -e tools/llmtool`.
- Ran `make -C tools/llmtool check` using that virtual environment: syntax check passed and all 95 unit tests passed.
- Configured the CLI using `llmtool configure` with a temporary `TEST_HOME`; the real user config was not accessed or changed.
- Used no API key. The generated config had mode `0600` and was outside the repository.
- The sandbox initially denied outbound and loopback sockets. Tests and live requests were rerun with network access permitted.

## Gateway connectivity

The read-only request to `http://192.0.2.10:4000/v1/models` connected successfully and returned **HTTP 200**. The response advertised model/route `gw`.

The `configure` wizard saved the intended settings:

```yaml
providers:
  default:
    type: openai-compatible
    base_url: http://192.0.2.10:4000
    model: gw
    timeout_seconds: 120.0
    api_key: null
    api_key_env: null
    provider_options:
      enable_thinking: false
generation:
  temperature: 0.0
  max_tokens: 2000
active_provider: default
```

`llmtool config show` displayed these settings, with no credentials present. Its config path was under the temporary `TEST_HOME`.

## CLI requests

| Command | Result |
|---|---|
| `llmtool ask "Reply with exactly: LLMTOOL_OK" --verbose` | Exit 0. Stdout was exactly `LLMTOOL_OK`. Stderr contained elapsed time, effective model metadata (`qwen3.6-35b-a3b`) and token counts only. |
| `llmtool context-pack` with the supplied facts | Exit 0. Preserved the permission-denied observation; treated the one-time DNS retry as a separate observation, not a root cause. Stderr was empty. |
| `llmtool analyze-log` with the supplied error and warning | Exit 0. Included `ROOT_CAUSE`, `EVIDENCE`, `DISTRACTORS`, `UNKNOWN_OR_UNPROVEN`, and `NEXT_COMMANDS`. Stderr was empty. |

No reasoning text or credential values appeared in captured stdout/stderr. No suggested command was executed.

## Observation

The `analyze-log` response suggested `cat /etc/company/app/config.yaml` as a `NEXT_COMMANDS` item. That command was not run. Reading a configuration file may reveal sensitive contents, so this suggestion should be reviewed before anyone executes it.

## Final status

**Gateway reachable:** Yes
**configure / config show:** PASS
**ask / context-pack / analyze-log:** PASS
**Reasoning leaked:** No observed leakage
**Secrets leaked:** No
**Repository code changed by the smoke test:** No
