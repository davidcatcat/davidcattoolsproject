# A13 Evidence Note: State-Location Boundary

Scope: documentation only. No code, runtime, environment or CI changes. This note records
current behavior at a pinned commit; it does not choose a universal state directory and does
not move or modify user data.

- Pinned main: `d4380aed191a9bc534cce3adbd610563b55a8680`
- Code references name file and function only; no line anchors.

## Historical anchors

- OSR-17, recorded in #396 comment 6552370022: historical observation of these defaults at the
  time of that snapshot.
- #2793, Coordinator comment 7210501920: historical cross-store observation.

The historical claim that all components ignore XDG state variables is a snapshot statement and
is not repeated here. Current per-component behavior differs: the diagnostics branch does
consult a valid `XDG_STATE_HOME` on Linux, while the two store defaults below do not.

## Default vs explicit resolution

### Task store — `task_store.py`

- Default: `default_root` resolves the user home plus `.local/state/llmtool/tasks`.
- Explicit: `TaskStore` accepts an explicit root, which supersedes the default.
- The default does not consult `XDG_STATE_HOME`.

### PM runtime — `pm_runtime.py`, `pm_runtime_codex.py`

- The session state root step in `pm_runtime.py` and `pm_runtime_codex.py` use a supplied
  `state_root` or the user home plus `.local/state/llmtool/pm-runtime`.
- The default does not consult `XDG_STATE_HOME`.

### Diagnostics — `diagnostics_settings.py`, `resolve_diagnostics_home`

Compared with the two store defaults, the diagnostics location resolution is different:

- Explicit override: `LLMTOOL_HOME`, when set, wins over the platform defaults.
- Darwin: user home plus `Library/Application Support/llmtool`.
- Linux (platform prefix `linux`): a valid absolute `XDG_STATE_HOME` is used when present and
  valid; otherwise the user-home state default, user home plus `.local/state`; then
  `llmtool` is appended.
- Candidate values pass the `_absolute` validator in the same file (non-empty string, bounded
  length, UTF-8, no unsafe control characters, absolute path, no `..` parts).
- The function resolves a location only: no expansion, no I/O, no store selection. The
  Windows branch exists in code; this note makes no Windows support claim from that branch.
- A diagnostics home override does not relocate any other llmtool store (task store,
  pm-runtime); it applies to the diagnostics location only.

## Comparison

| Component | Current source (file, function) | Platform behavior | Stage handoff |
|---|---|---|---|
| Task store | `task_store.py`: `default_root`, `TaskStore` | Default `~/.local/state/llmtool/tasks`; explicit root accepted by `TaskStore`; no `XDG_STATE_HOME` in the default | A13.1: project state selection and migration |
| PM runtime | `pm_runtime.py` (session state root step), `pm_runtime_codex.py` | Supplied `state_root` or default `~/.local/state/llmtool/pm-runtime`; no `XDG_STATE_HOME` in the default | A13.1: project state selection and migration |
| Diagnostics | `diagnostics_settings.py`: `resolve_diagnostics_home` | `LLMTOOL_HOME` if set; Darwin `~/Library/Application Support/llmtool`; Linux valid absolute `XDG_STATE_HOME` else `~/.local/state/llmtool`; Windows branch present, no support claim made | A13.3: isolation verification; installed-environment promise waits for its existing acceptance stage |

## Boundary and gates

- Project state selection and migration belong to A13.1; isolation verification belongs to
  A13.3. Any installed-environment promise waits for its existing acceptance stage.
- No universal state directory is chosen and no user data is changed by this note.
- Gates: exact-head hosted docs check at the pinned main; formal review by codex-reviewer.
