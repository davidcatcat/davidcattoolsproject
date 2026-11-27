# A13 D6 CLI Refusal Boundary — missing-root path

Status: **Advisor-verified** at pinned main by static inspection. No runtime test was run for this note.
Pinned main: `e59bbda9f0ccc72a6296fdd684a691f54b6ea431`.
Evidence: #396 OSR-11 comment 6552370022 (historical missing-root traceback report) and #2793 Coordinator comment 7210501920 (verification record). All verified facts below were checked by the Advisor at pinned main. Source identifiers name file and symbol only, no line anchors; machine-specific paths are omitted.
Context: `a13-platform-assumptions.md` (A13 platform assumptions inventory). This note does not restate the trust-schema, Git layout, or tool lookup notes, and it introduces no new policy.

## Comparison: historical traceback vs current CLI refusal

| Aspect | Historical path (#396, OSR-11) | Current path at pinned main |
|--------|--------------------------------|------------------------------|
| Trigger | Missing repository root in the D6 worktree lifecycle | Same missing-root condition |
| Behaviour | Unhandled `FileNotFoundError` surfaced to the user as a raw traceback | The handler catches the `FileNotFoundError` (missing root), prints the fixed STOP with error code `D6_REPOSITORY_INVALID`, then exit 1 |
| Basis | Reported in #396 comment 6552370022 | Static inspection by the Advisor, recorded in #2793 comment 7210501920 |

## Verified facts at pinned main (static inspection)

| # | Source identifier (file · symbol) | Verified fact |
|---|-----------------------------------|---------------|
| 1 | `d6_worktree_lifecycle.py` · `_main` | In the main step, when the repository root is omitted, it is filled from the trusted `canonical_root`. |
| 2 | `d6_worktree_lifecycle.py` · `create_worktree` | Resolves the repository root with `strict=True`; a missing root surfaces as `FileNotFoundError` on this path. |
| 3 | `d6_worktree_lifecycle.py` · `_main` (outer handler) | Catches the `FileNotFoundError` from the missing root, prints the fixed STOP with `D6_REPOSITORY_INVALID`, and exits 1; no traceback is emitted on this caught path. |
| 4 | `d6_worktree_lifecycle.py` · `_main` (outer handler) | `LifecycleError` and `TrustError` print their error codes at the CLI boundary, not the exception text. |

## Boundary: CLI handler vs direct library calls

The refusal above is a property of the `_main` outer handler in `d6_worktree_lifecycle.py`. A caller that invokes the D6 lifecycle library directly, without that handler, is not protected by this boundary: `FileNotFoundError` and the lifecycle/trust errors propagate to such a caller as ordinary exceptions. The verified handler behaviour covers `FileNotFoundError`, `LifecycleError`, and `TrustError`; this note makes no claim about other exception types — a strict resolve can raise other `OSError` subclasses for invalid paths — or about which error paths inside the library are caught or wrapped.

## Scope limits

- The historical missing-root traceback path is marked **addressed by static inspection** at pinned main.
- The verified refusal behaviour is limited to the missing-root `FileNotFoundError`. That a strict resolve can raise other `OSError` subclasses for invalid paths is recorded as out of scope; this note does not claim those are caught or produce the STOP.
- This note does **not** claim that the historically proposed replacement code was adopted, that all error paths are caught, or that a runtime test was run.
- No new policy is created; the refusal behaviour above is recorded as evidence only.

## A13 baseline

This refusal boundary (fixed STOP + `D6_REPOSITORY_INVALID` + exit 1; code-only output for `LifecycleError` / `TrustError`) is recorded as the **existing baseline** for:

- A13.1 project registration tests: expected refusal output for the missing-root path (fixed STOP, `D6_REPOSITORY_INVALID`, exit 1).
- Installed CLI acceptance: no traceback, code-only output, exit 1, for the missing-root refusal.

Refusal-flow tests against this baseline must mock, monkeypatch, or inject the `screen` function while still checking real behaviour; test data carries no credential-shaped strings.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
