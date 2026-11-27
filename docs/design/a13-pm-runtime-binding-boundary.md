# A13 PM Runtime Binding Boundary — historical to current

Status: short historical-to-current comparison of PM runtime selection and the installed-runtime evidence still needed. Not a full runtime audit.
Pinned main: `d4380aed191a9bc534cce3adbd610563b55a8680`.
Evidence: #396 comment 6552370022 (OSR-15) and #2793 Coordinator comment 7210501920. All facts below were verified by Advisor at the pinned main; source identifiers name file and symbol only, no line anchors.
Context: A13 framing and the verified subset inventory live in `docs/design/a13-platform-assumptions.md`; this note does not repeat them.

## Selection boundary: from Claude-only to two accepted runtimes

Historically PM creation was Claude-only: a single runtime, no selection, and the binding carried only the Claude account. At the pinned main the boundary is wider:

- `pm_runtime.py` · `create_dedicated_pm` accepts `claude-code` and `codex`, stores the selected runtime together with the resolved account, and rejects any other runtime with `PM_RUNTIME_UNSUPPORTED`. The default remains `claude-code`.
- `pm_runtime.py` · `open` delegates a `codex` binding to `pm_runtime_codex.py` · `CodexRuntime`; `CodexRuntime.open` checks the dedicated codex binding and otherwise refuses with `PM_SESSION_NOT_DEDICATED`.

`pm_runtime.py` · `PMRuntime.open` is recorded as the site of the Claude launch failure code `PM_PROCESS_START_FAILED` visible in the current source. It is Claude-side only: it is not generalized to both runtimes, and no supported version range is inferred from it.

Listing both runtimes in the creation boundary does not change which PM is authorized in a live repository: authorization follows the stored binding and its resolved account, not the menu of accepted runtimes.

## Still open: runtime support on a clean installed product

The historical Claude-only limitation is resolved at the selection boundary; runtime *support* is a different claim. Whether `claude-code` and `codex` actually launch and operate as dedicated PM runtimes on a clean installed product is not verified here and remains needed evidence. No runtime execution was performed for this note, and no runtime tests are claimed to have run.

## Source and acceptance

| ID | Source identifier (file · symbol) | Verified fact at pinned main | Acceptance still needed | Owning milestone |
|----|-----------------------------------|------------------------------|-------------------------|------------------|
| PM-RT.1 | `pm_runtime.py` · `create_dedicated_pm` | Accepts `claude-code` and `codex`; stores the runtime and resolved account; unsupported runtime refused with `PM_RUNTIME_UNSUPPORTED`; default `claude-code` | Installed diagnostics: confirm the accepted runtime set and the `PM_RUNTIME_UNSUPPORTED` refusal on a clean installed product | A13.4 |
| PM-RT.2 | `pm_runtime.py` · `open`; `pm_runtime_codex.py` · `CodexRuntime.open` | `codex` bindings delegate to `CodexRuntime`; non-dedicated codex binding refused with `PM_SESSION_NOT_DEDICATED` | Same installed-diagnostics milestone as PM-RT.1 | A13.4 |
| PM-RT.3 | `pm_runtime.py` · `PMRuntime.open` | Raises `PM_PROCESS_START_FAILED` on the Claude launch failure path; Claude-side only, no version range inferred | Actual user-owned session/provider evidence that the selected runtime launches and holds a dedicated session | A13.5 or A13.7 |

## Scope notes

- No runtime execution, no new config policy. Legacy review exchange and directory defaults are out of scope for this note.
- Docs-only: no code, CI, credentials, provider calls, or account changes; no actual session or account identifiers appear here.
- Test data rule (Owner #763 A): this note adds no tests. If refusal-flow tests are later added, they must mock, monkeypatch, or inject the screen function and still check real behaviour; only sensitive-detector tests may use the #981 synthetic-data exception (labelled synthetic, independently reviewed, never real credentials). No production check is disabled, bypassed, or weakened for test convenience.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
