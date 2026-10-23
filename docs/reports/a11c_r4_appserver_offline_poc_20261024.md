# R4 offline App-server D8 verification

**Task:** `a11c-r4-offline-appserver-proof-20261024`
**Base:** `79d0b44d6f6f2be7de779ba4a25893eea6934262`
**Retained R4 candidate:** `d886d79e192ec22fe1ba5e001012977d7ea9df06` in the separately locked `a11c-appserver-r4-20261023` worktree. It was read only; its dirty files and lock were preserved.
**Installed CLI:** Codex CLI `0.163.0`; local binary path omitted from this public report.

## Result

The retained R4 adapter does **not** currently provide a D8-compatible App-server execution route. The offline probes found two critical direct-route failures:

1. A successful Codex App-server turn sent one local `POST /v1/responses` with synthetic auth, but the request had no `max_output_tokens`. The retained adapter also has no reference to `RunBudget` and does not start a Responses intermediary.
2. A deliberately malformed loopback Responses reply caused the installed App-server to send **six** upstream POSTs for one turn (one initial request plus five automatic retries). This exceeds D8's maximum of one retry. A local intermediary would have to count and reject each request before forwarding it; it cannot assume one App-server turn equals one provider request.

These findings came from the installed Codex binary configured with a fresh temporary `CODEX_HOME`, a temporary `auth.json` containing only a synthetic key, and a Responses endpoint bound to `127.0.0.1`. No real credentials or external Provider were used. The failed adapter handshake made no Provider request.

## Evidence by control

| Control | Result | Evidence and limit |
|---|---|---|
| Minimal isolated `CODEX_HOME` | **PASS, initialization only** | A fresh temporary home with an empty `mcp_servers` table, analytics disabled, and synthetic auth initialized over stdio. It was deleted by temporary-directory cleanup. |
| Loopback Responses request | **PASS, routing only** | The installed App-server completed one turn through `http://127.0.0.1:<ephemeral-port>/v1`; captured auth matched only the synthetic test value. No external endpoint was configured. |
| Effective output-token cap | **FAIL** | Captured successful upstream request had no `max_output_tokens`. The retained R4 adapter has no output-cap or proxy code. |
| Loopback middleware cap insertion/clamping | **PASS for PoC only** | Socket-captured provider bodies contained the inserted budget cap or the smaller caller cap. This fixture is not wired into R4. |
| RunBudget request/retry bound | **FAIL** | One malformed response produced six upstream requests. The adapter has no `RunBudget` admission. |
| Loopback middleware pre-forward call bound | **PASS for PoC only** | Retry/reconnect-style POSTs through the socket were forwarded at most twice under a two-call RunBudget; later requests were denied before reaching the Mock Responses server. |
| `--disable unified_exec` flag support | **PASS for installed CLI** | `codex app-server --help` exposes `--disable <FEATURE>`; a direct local run accepted `--disable unified_exec` and `--disable shell_tool`. This does not show the retained adapter applies the flag. |
| Retained adapter disables `unified_exec` | **FAIL** | Its `_APP_SERVER_POLICY_ARGS` disables `shell_tool`, but not `unified_exec`. |
| Shell/exec tool exposure is absent | **BLOCKED** | The direct mock request still contained five tool entries. Their types were not captured, so this run does not establish whether shell/exec tools were exposed or unavailable. No tool call was requested. |
| Loopback middleware tool boundary | **PASS for PoC only** | Requests with non-empty synthetic shell, exec, or MCP `tools` entries were rejected before forwarding. Only sanitized `tools_present` and `tool_count` were retained; absent/empty tool lists were allowed. This does not prove App-server MCP setup or tool exposure. |
| MCP is not initialized or spawned | **BLOCKED** | The isolated home had no inherited MCP configuration, but no process-level MCP initialization/spawn audit was captured. `mcp_servers={}` alone is not treated as proof. |
| Alternate endpoint / redirect / WebSocket / reconnect bypass | **BLOCKED for R4; loopback PoC PASS only** | The socket-level intermediary rejects non-Responses paths, non-POST methods, WebSocket upgrades, redirects, and requests beyond budget. The retained adapter is not connected to this intermediary, so its actual bypass resistance is unproven. |
| Success/failure cleanup | **PASS for exercised local processes** | The successful raw App-server process exited 0; the malformed-response process also exited 0 after stdin close. Both local mock servers were shut down. The retained adapter's targeted mock-CLI lifecycle tests passed. |
| Timeout/disconnect replay | **PASS for loopback PoC only** | A socket timeout and provider disconnect become UNKNOWN and prevent a second forward. Real App-server timeout/disconnect cleanup through the retained adapter was not demonstrated. |
| Exact adapter / installed CLI compatibility | **BLOCKED** | `CodexAppServerClient` rejected the 0.163.0 initialize response as `APP_SERVER_MESSAGE_INVALID`: the response had no `jsonrpc` field and supplied `userAgent`/`codexHome`, not the expected `serverInfo.version`. It failed closed before a Provider request. |

The installed CLI help advertises `stdio://`, and the successful raw CLI probe used stdio. That does not establish the retained adapter can speak this installed CLI's current protocol. No production path or credential relay is approved by this report.

## Socket-level loopback intermediary PoC

The repair changes `tools/llmtool/tests/test_codex_app_server_offline_poc.py` to run two actual HTTP servers, each bound to `127.0.0.1` and an OS-assigned ephemeral port. The middleware forwards real HTTP POST bodies to the local Mock Responses provider over a socket. It copies only the fixed synthetic bearer and JSON content type; it does not accept a configurable upstream host. The mock provider records request paths, headers, and bodies. No real Codex App-server turn was run during this repair.

The socket PoC tests, using the repository's existing `RunBudget` predicates, verify:

- output-cap insertion and clamping to the remaining output-token budget are visible in the bodies captured by the local provider;
- every forwarded request has the fixed synthetic bearer and `/v1/responses` path;
- retry/reconnect-style and concurrent POSTs traverse the proxy, while requests past the RunBudget limit are rejected before forwarding;
- a local 302 response is returned as blocked, and its local `Location` is never requested;
- alternate paths, GET/upgrade requests, non-synthetic auth, and non-empty shell/exec/MCP tool lists never reach the provider;
- absent and empty `tools` lists are allowed, while tool rejection records only a boolean and count, not raw descriptors;
- timeout and disconnect create UNKNOWN usage and prevent replay before a second provider forward;
- both listener sockets are closed and their serving threads joined after each test.

The loopback intermediary itself now has socket-level evidence. It remains a proof fixture only; it is not production code and the retained R4 adapter is not wired through it. Consequently, this does not establish that Codex cannot use another endpoint or bypass the proxy. The exact-candidate source diagnostic is opt-in through `LLMTOOL_R4_CANDIDATE_ROOT` and binds the observed missing controls to the retained source.

## Commands and checks

CLI inspection:

```text
codex --version
codex app-server --help
codex app-server daemon --help
codex app-server proxy --help
```

Installed version: `codex-cli 0.163.0`. The local loopback probe used the installed binary with a temporary `CODEX_HOME`, synthetic `OPENAI_API_KEY`, `mcp_servers={}`, analytics disabled, and a provider URL bound to `127.0.0.1`. One success run captured one request with `max_output_tokens=None`; a malformed-response run captured six requests. The exact retained adapter attempt stopped at initialize with `APP_SERVER_MESSAGE_INVALID` before provider egress.

New proof tests:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/llmtool \
  LLMTOOL_R4_CANDIDATE_ROOT="$R4_CANDIDATE_ROOT" \
  python3 -B -m unittest discover -s tools/llmtool/tests \
  -p 'test_codex_app_server_offline_poc.py' -v
```

Result: **9/9 PASS**. The tests sent only synthetic requests through the two loopback servers; no external network requests were made. `R4_CANDIDATE_ROOT` is set from the private D6 registry when running the opt-in source diagnostic; no local worktree path is recorded here.

Retained R4 candidate's focused App-server tests, run with bytecode writes disabled and using its existing fake CLI fixtures:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/llmtool \
  python3 -B -m unittest discover -s tests -p 'test_codex_app_server.py' -v
```

Result: **17/17 PASS**, including stdio client cleanup, child exit/reap, and durable no-resend recovery. These tests use mock children; they are not evidence of current installed-CLI compatibility.

The socket PoC and report are test-only additions based on the exact base SHA; neither is wired into production source. Tracked diff check and explicit whitespace/final-newline validation passed before publication. Real Provider calls: **0**. Provider spend: **0**. No additional full Codex App-server turns were run during repair.

## Next technical step

Keep the R4 candidate `RETAIN_BLOCKED`. A follow-on candidate must add a real local intermediary seam that owns every upstream POST, injects a RunBudget-bounded `max_output_tokens`, and counts/rejects before forwarding. It must also pin endpoint/transport behavior, make MCP absence observable, disable both `unified_exec` and shell/exec tools, and test normal, failed, timed-out, disconnected, and shutdown paths. First resolve the App-server protocol mismatch against a pinned CLI version. Any production credential forwarding remains a separate authority/review decision.
