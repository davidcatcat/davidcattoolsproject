# VPN ON Final E2E Smoke Test

**Date:** 2026-09-29 17:12 Asia/Taipei

## Results

| Check | Result | Evidence |
|---|---|---|
| Company LLM `/v1/models` | **PASS (carried forward)** | Earlier VPN ON probe in this conversation returned HTTP 200; JSON parsed successfully and exposed model/route entry `gw`. This endpoint was not re-probed during the final E2E run. |
| Company LLM inference | **SKIPPED-CONFIG** | Redacted `llmtool config show` showed active provider `default`, type `openai-compatible`, with `base_url: null` and `model: null`. No config was changed and no inference request was sent. |
| Company LLM verbose metadata | **SKIPPED-CONFIG** | No inference request was sent, so stdout/stderr metadata behavior was not exercised. |
| Company route | **PASS** | Route lookup for `10.29.7.58` selected interface `utun4`; a gateway was present. No route changes were made. |
| Codex CLI backend | **PASS** | The current Codex session continued to receive model responses. Local command `echo CODEX_LOCAL_EXEC_OK` succeeded. No nested Codex session was started. |
| GitHub repository access | **SKIPPED-AUTH** | `git ls-remote` ran with credential helpers disabled and prompts suppressed. GitHub requested a username; no credentials were searched for or used. |

The required `tools/llmtool/.venv` did not exist, so a local venv was created and `pip install -e tools/llmtool` completed. The checkout's `llmtool providers` command returned `invalid command or options`; this did not change settings. The venv is ignored by Git.

## Overall

**INCOMPLETE.** Company inference was skipped because the active profile has no endpoint or model, and GitHub repository authorization was not verified. Therefore `SAME-HOST ORCHESTRATION NETWORK PREREQUISITE` is not marked PASS.

No repository source files, VPN, DNS, route, proxy, or system settings were changed. The tested checkout had pre-existing working-tree changes before the run; the final `git status --short` showed the same entries. No credentials, tokens, keychain data, `.env`, or SSH keys were read or output.
