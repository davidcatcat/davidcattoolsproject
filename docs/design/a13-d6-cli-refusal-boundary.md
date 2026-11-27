# A13 D6 CLI Refusal Boundary — missing-root path

Status: **Advisor-verified** at pinned main by static inspection. No runtime test was run for this note.
Pinned main: `e59bbda9f0ccc72a6296fdd684a691f54b6ea431`.
Evidence: #396 OSR-11 comment 6552370022 (historical missing-root traceback report) and #2793 Coordinator comment 7210501920 (verification record). All verified facts below were checked by the Advisor at pinned main. Source identifiers name file and symbol only, no line anchors; machine-specific paths are omitted.
Context: `a13-platform-assumptions.md` (A13 platform assumptions inventory). This note does not restate the trust-schema, Git layout, or tool lookup notes, and it introduces no new policy.

## Comparison: historical traceback vs current CLI refusal

| Aspect | Historical path (#396, OSR-11) | Current path at pinned main |
|--------|--------------------------------|------------------------------|
| Trigger | Missing repository root in the D6 worktree lifecycle | Same missing or invalid root condition |
| Behaviour | Unhandled `FileNotFoundError` surfaced to the user as a raw traceback | Fixed STOP output carrying error code `D6_REPOSITORY_INVALID`, then exit 1 |
| Basis | Reported in #396 comment 6552370022 | Static inspection by the Advisor, recorded in #2793 comment 7210501920 |

## Verified facts at pinned main (static inspection)

| # | Source identifier (file · symbol) | Verified fact |
|---|-----------------------------------|---------------|
| 1 | `d6_worktree_lifecycle.py` · main step | When the repository root is omitted, the main step fills it from the trusted `canonical_root`. |
| 2 | `d6_worktree_lifecycle.py` · `create_worktree` | Resolves the repository root with `strict=True`; a missing or invalid root surfaces as `FileNotFoundError` on this path. |
| 3 | D6 CLI · outer handler | Catches `FileNotFoundError`, prints the fixed STOP with `D6_REPOSITORY_INVALID`, and exits 1; no traceback is emitted on this path. |
| 4 | D6 CLI · outer handler | `LifecycleError` and `TrustError` are treated the same at the CLI boundary: their error codes are printed, not the exception text. |

## Boundary: CLI handler vs direct library calls

The refusal above is a property of the CLI entry point's outer handler. A caller that invokes the D6 lifecycle library directly, without that handler, is not protected by this boundary: `FileNotFoundError` and the lifecycle/trust errors propagate to such a caller as ordinary exceptions. This note makes no claim about which error paths inside the library are caught or wrapped.

## Scope limits

- The historical missing-root traceback path is marked **addressed by static inspection** at pinned main.
- This note does **not** claim that the historically proposed replacement code was adopted, that all error paths are caught, or that a runtime test was run.
- No new policy is created; the refusal behaviour above is recorded as evidence only.

## A13 baseline

This refusal boundary (fixed STOP + `D6_REPOSITORY_INVALID` + exit 1; code-only output for `LifecycleError` / `TrustError`) is recorded as the **existing baseline** for:

- A13.1 project registration tests: expected refusal output when root resolution fails.
- Installed CLI acceptance: no traceback, code-only output, exit 1.

Refusal-flow tests against this baseline must mock, monkeypatch, or inject the screen (output) function while still checking real behaviour; test data carries no credential-shaped strings.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
