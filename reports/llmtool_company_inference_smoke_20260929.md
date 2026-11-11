# VPN ON Company LLM Inference Smoke

**Date:** 2026-09-29

**Tested checkout:** `main` at `e1af4b4` (`docs: add VPN ON connectivity smoke report`)

## Setup

- `tools/llmtool/.venv/bin/llmtool` was initially absent in the clean main worktree. A local venv was created and `pip install -e tools/llmtool` succeeded.
- `llmtool --version` returned `0.1.0`; `llmtool providers` listed `openai-compatible`.
- Inference used a newly created temporary HOME, not the real user HOME or config. The temporary profile was `company-qwen`, type `openai-compatible`, base URL `http://192.0.2.10:4000`, model `gw`, timeout 30 seconds, no API key or key environment variable, and `enable_thinking: false`.
- Redacted `config show` matched the requested profile. The temporary HOME was removed after testing.

## Inference results

| Check | Result | Evidence |
|---|---|---|
| Basic inference | **PASS** | Exit code 0; stdout was exactly `COMPANY_LLM_OK`; stderr was empty. |
| Verbose inference | **PASS** | Exit code 0; stdout was exactly `COMPANY_LLM_VERBOSE_OK`. |
| Verbose stderr | **PASS** | Only safe metadata: `model=qwen3.6-35b-a3b`, `prompt_tokens=132`, `completion_tokens=7`, `total_tokens=139`, `elapsed_seconds=0.307`. |
| Reasoning leak | **NO** | No reasoning fields or reasoning content appeared in stdout/stderr. |
| Prompt echo | **NO** | Full prompt was not echoed. |
| Credential leak | **NO** | No credential was configured, read, or emitted. |
| VPN route | **PASS** | Route to `192.0.2.10` used interface `utun4`; gateway was present (`10.123.144.1`). |
| Company `/v1/models` | **PASS (prior probe)** | Earlier VPN ON connectivity probe in this conversation returned HTTP 200 and valid model/route JSON. |

## Overall

**COMPANY LLM INFERENCE = PASS**

**SAME-HOST ORCHESTRATION NETWORK PREREQUISITE = PASS**

The real company model inference completed through llmtool over the VPN route. The current Codex session remained connected and executed local commands; prior VPN checks recorded GitHub HTTPS as reachable. Claude backend verification is carried forward from the user's prior validation.

No repository source files, real user config, credentials, VPN, DNS, route, proxy, or system settings were modified. The temporary HOME was deleted. The venv is a local ignored development artifact.
