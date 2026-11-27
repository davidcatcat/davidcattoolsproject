# A13 Provenance Note: Doctor Readiness Boundary

## Provenance

- Pinned to `main` at commit `d4380aed191a9bc534cce3adbd610563b55a8680`.
- Addresses the offline doctor-readiness portion of the work tracked under issue #396 (OSR-10), per #396 comment 6552370022.
- Coordinator boundary for what this note may and may not certify: #2793, comment 7210501920.
- Scope of this note: documentation only. It records what the current offline doctor covers, which historical OSR-10 behavior that corresponds to, and which product acceptance still has to happen separately.

## What is addressed: `doctor_output` and `run_doctor` (tools/llmtool/llmtool/doctor.py)

The current doctor is an offline inspection only. `doctor_output` buffers the complete safe projection before touching stdout and reports three criteria:

1. Configured provider readiness: the configuration `readiness` value from `inspect_config`, alongside `provider_type`.
2. Applicable executable presence: for the `claude-cli` and `codex-cli` provider types, the corresponding executable is looked up the same way `complete()` requires lookup and an absolute path, but it is never invoked. The report records `available` or `missing`. For other provider types the field is `not-applicable`.
3. Credential-presence criteria: the credential state is `required` for `openai`, and for `openai-compatible` unless it is plainly unconfigured with ready readiness; it is `not-applicable` for the CLI provider types. Credential readiness means "configured" or "configured and resolvable", never merely "env-configured-absent".

Overall status is `ready` only when all three criteria hold: readiness `ready`, executable not `missing`, and credential ready. Otherwise it is `not-ready`. In particular, a missing executable can yield overall `not-ready` even when configuration readiness is `ready`.

Exit behavior in `run_doctor` is:

- A degraded diagnostics metadata finding is a real nonzero result, never a silent pass.
- Strict not-ready: `strict=True` returns nonzero when overall is `not-ready`.
- Non-strict not-ready: `strict=False` may still return zero when overall is `not-ready`.
- Read errors (`ReaderError`) and other unexpected store failures are surfaced with nonzero results.
- `BrokenPipeError` is re-raised by `run_doctor` rather than converted to a nonzero return; the CLI layer handles the re-raised broken pipe separately and does not report it as a nonzero doctor exit.

This is the part of OSR-10's offline diagnostic intent that the current implementation addresses: an overall readiness verdict and a strict/offline exit-code contract, computed without any network, provider, inference, or credential use.

## What is NOT addressed and needs separate product acceptance

`doctor_output` and `run_doctor` do not:

- Execute any inference;
- Authenticate against any provider or route;
- Verify D6 dispatch;
- Authorize any project;
- Inspect credential values (only credential-presence criteria are used).

A healthy offline report therefore cannot supply route, authentication, dispatch, or authorization receipts. Those receipts require live, user-owned execution and belong to separate product acceptance, not to this note.

Outstanding work to be recorded and accepted separately:

- Future clean-install diagnostics under A13.4.
- Live user-owned routes under A13.5 and A13.7.

Until those sections are accepted, the doctor's overall and strict behavior stands on its own as the offline boundary described above.

## Limits honored in this note

- Docs only: no command runs, provider calls, live checks, credential inspection, code, or workflow modifications accompany this note.
- No environment values or historical paths are included.
- Source references name files and functions only (e.g., `tools/llmtool/llmtool/doctor.py`: `doctor_output`, `run_doctor`); no line anchors.
- Gates: the exact-head hosted docs check applies to this note's commit HEAD; `d4380aed191a9bc534cce3adbd610563b55a8680` is the main source pin referenced by the note. Formal review by codex-reviewer.
