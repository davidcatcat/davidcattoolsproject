# VPN ON Connectivity Smoke Test

**Date:** 2026-09-29 17:01 Asia/Taipei

**Environment:** macOS Darwin 24.6, x86_64
**Scope:** Read-only connectivity checks. No repository implementation, VPN, DNS, route, proxy, or system settings were changed. No credentials were read or sent.

## Results

| Service | Result | Evidence |
|---|---|---|
| Company LLM gateway `/v1/models` | **PASS** | TCP connected; HTTP 200 in 0.252 s. Response parsed as JSON and contained model/route information (`gw`). The temporary response file was removed. |
| Company LLM inference | **SKIPPED** | `llmtool config show` succeeded and is redacted. The active provider was not configured for the company gateway, so no config was changed and no inference request was sent. |
| Codex CLI backend | **PASS** | The current Codex session continued to receive this prompt and execute commands. No nested Codex process was started. |
| OpenAI HTTPS | **PASS** | `https://api.openai.com/v1/models` returned HTTP 401 without authorization. DNS, TCP and TLS succeeded; total 0.495 s. No API key or paid inference was used. |
| GitHub HTTPS | **PASS** | `https://github.com/` returned HTTP 200; TCP and TLS succeeded; total 0.911 s. |
| GitHub repository access | **SKIPPED-AUTH** | `git ls-remote` did not use credential helpers or interactive prompts. GitHub requested a username, so repository authorization was not tested. No credentials were searched for. |
| DNS | **PASS** | `api.openai.com` and `github.com` resolved successfully. |
| Default route | **PRESENT** | A default route, gateway, and interface (`en0`) were reported; no route changes were made. |

## Overall

**PASS for simultaneous service network reachability** across the company gateway, current Codex session, OpenAI HTTPS, and GitHub HTTPS. Company inference and GitHub repository authorization remain unverified because the active company profile was not selected and no credentials were used.

No repository files were changed during the connectivity test. The only test artifact, `/tmp/llmtool-company-models.json`, was removed.
