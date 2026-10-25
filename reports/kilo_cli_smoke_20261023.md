# Kilo CLI smoke test report (2026-10-23)

## Result

- **Headless Kilo inference: PASS.** Kilo returned the exact requested marker from a fresh headless session.
- **Attach to the user's Kilo TUI: FAIL / not delivered.** The attach attempt returned `Session not found`; there is no evidence the prompt reached inference or appeared in the TUI.
- **llmtool Provider/Worker integration: NOT TESTED.** The headless CLI smoke test does not establish llmtool integration or end-to-end Developer execution.

## Headless inference evidence

- CLI: Kilo `7.8.15` (`/Users/davidsun/.kilo/bin/kilo`).
- Invocation used a mode-0700 temporary working directory, `--pure`, `--agent summary`, JSON output, and a fresh session.
- Prompt: `Reply with exactly KILO_SMOKE_OK and nothing else.`
- Result: exit status 0; exact response `KILO_SMOKE_OK`.
- Model reported by Kilo: `kilo/reflection/beam`.
- Usage reported by Kilo: 762 total tokens (406 input, 9 output, 27 reasoning, 320 cache read); reported cost 0; elapsed time about 444 ms.
- The reported cost is Kilo's response metadata only. It was not independently checked against a provider invoice or billing ledger.
- No repository content was included in the prompt. The smoke test was a new headless session, not an attached TUI session.

## TUI attach attempt

The user started Kilo with `--port 4096`. A local listener was observed on `127.0.0.1:4096`. The following attach attempt was made with a separate marker prompt:

```sh
kilo run --attach http://127.0.0.1:4096 --dir <repository> --agent summary --format json --title kilo-tui-smoke 'Reply with exactly KILO_TUI_VISIBLE_OK and nothing else.'
```

The command exited 1 with `Error: Session not found`. A separate request to `/global/health` received `401 Unauthorized` with a Basic authentication challenge. The Codex process environment did not contain `KILO_SERVER_PASSWORD` or `KILO_SERVER_USERNAME` (presence only was checked; no credentials were read or recorded). Therefore this attempt did not verify that a prompt could be sent to or seen in the user's TUI. The user later exited Kilo, and port 4096 was observed free.

Kilo's CLI documentation describes server attach and authentication options: <https://kilo.ai/docs/code-with-ai/platforms/cli-reference>.

## Conclusion and next verification

The CLI can perform a basic headless inference successfully. TUI attach remains unverified because the server rejected the unauthenticated health request and the attach command could not find a session. No password or other credential is included in this report. A valid, authorized server session must be available before repeating the attach check. This result does not claim a successful llmtool integration, a successful TUI handoff, or independently verified provider cost.
