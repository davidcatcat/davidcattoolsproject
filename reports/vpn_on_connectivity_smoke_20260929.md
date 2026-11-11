# VPN ON Connectivity Smoke Test

**Date:** 2026-09-29 17:01 CST
**Host:** macOS (Darwin 24.6.0, x86_64)
**Result:** PASS. With the company VPN on, the company gateway, external HTTPS and GitHub were reachable at the same time. Company LLM inference was skipped.

This was a read-only network check. No repository files, DNS, routes, VPN, proxy or system settings were changed during the test. No credentials, tokens, keychain entries, `.env` files or SSH keys were read. No `Authorization` header was sent.

## Matrix

| Check | Result | Evidence |
|-------|--------|----------|
| Company LLM `/v1/models` | **PASS** | HTTP 200, connect 0.13 s, total 0.26 s; valid JSON; model id `gw`, `default_model` `gw` |
| Company LLM inference | **SKIPPED** | `tools/llmtool/.venv/bin/llmtool` does not exist, and installing packages was out of scope |
| Session model backend | **PASS** | The agent session kept receiving prompts, running commands and replying |
| OpenAI HTTPS | **PASS** | `https://api.openai.com/v1/models` returned HTTP 401 without credentials, which is expected; remote IP 162.159.140.245, connect 0.25 s, TLS 0.39 s, total 0.72 s |
| GitHub HTTPS | **PASS** | `https://github.com/` returned HTTP 200; connect 0.24 s, TLS 0.36 s, total 1.16 s |
| GitHub repo access | **PASS** | `git ls-remote https://github.com/davidcatcat/davidcattoolsproject.git HEAD` returned `a812ce66153b97dedb1e860a7fae267fbc284dc0` |
| DNS | **PASS** | `api.openai.com` resolved to 172.66.0.243; `github.com` resolved to 20.27.177.113 |
| **Overall simultaneous connectivity** | **PASS** | All reachability checks passed together; only inference was skipped |

## Routing

The VPN appears to be split-tunnel.

- The default route uses `en0`, gateway 192.168.1.1.
- The route to `192.0.2.10` uses the VPN interface `utun4`, gateway 10.123.144.1.

## Notes

- The session running this test was Claude Code, not Codex CLI. The "session model backend" row therefore shows that this session's own model connection worked. Codex CLI itself was not tested, and no nested `codex` or `codex exec` was started.
- The `/v1/models` response was saved to a `mktemp -d` directory in the session scratchpad instead of `/tmp/llmtool-company-models.json`. It was not deleted, which follows the no-`rm` rule. Only the model ids and `default_model` were printed.
- To test inference later, install llmtool into `tools/llmtool/.venv`, confirm that `llmtool config show` shows the company profile as active, then run `llmtool ask "Reply with exactly: COMPANY_LLM_OK"`.
