# Issue #63 items 2–3: test-excerpt false-rejection measurement (2026-11-03)

Scope: `llmtool test-excerpts` (`tools/llmtool/llmtool/test_output_excerpts.py`).
Evidence: `tools/llmtool/tests/test_test_output_excerpts_fidelity.py`, run locally on
macOS with the repo venv (Python 3.13.3). Everything ran offline: no provider, network or config access,
and all secret-shaped values are assembled from fragments at runtime.
These are fixed, curated denominators. They are not a production precision/recall claim.

## Corpus

| Family | Count | Source |
|---|---|---|
| unittest (supported) | 13 | Real runs of a throwaway stdlib test file in a temp dir (`-B -I`, stderr→stdout): plain, all-pass+skip+xfail, root logger, named logger, `Basic auth disabled`, `token = None`/`password: null`, 48 KiB+ repeated progress lines, Unicode, `-v`, `-v`+root log, subTests, 48 KiB+ assertion diff, buffered stdout |
| pytest (supported) | 11 | Checked-in sanitized pytest-8 layout (pytest is not installed in the venv): failures+errors, all-pass, warnings, `Basic auth disabled`, `token = None` stdout and `--showlocals`, captured log (`ERROR    root:…`), captured stderr (`ERROR:root:…`), 48 KiB+ repeated, Unicode, bare `OK` stdout |
| JUnit XML (unsupported) | 1 | A sample `--junitxml` document |

Every capture is benign. An ideal parser would mark all 24 supported captures COMPLETE.

## Results (benign captures not COMPLETE)

| Family | Before (main @ b0ec7f5c) | After this change |
|---|---|---|
| unittest | 5/13 = 38% (3 INCOMPLETE, 2 REJECTED) | 3/13 = 23% (2 INCOMPLETE, 1 REJECTED) |
| pytest | 6/11 = 55% (3 INCOMPLETE, 3 REJECTED) | 2/11 = 18% (1 INCOMPLETE, 1 REJECTED) |
| supported total | 11/24 = 46% | 5/24 = 21% |
| JUnit XML | 1/1 INCOMPLETE `unknown_format` (by design: unsupported, never parsed) | unchanged |

Hard rejections (no JSON output at all): 5/24 before, 2/24 after.

## Fixes (both sides are tested)

1. **Null/boolean keyword assignments.** `token = None`, `password: null`,
   `"secret": None`, `client_secret: true` and pytest `--showlocals` `token      = None`
   are no longer rejected. The exemption applies only when the **entire rest of the line** is
   `none|null|true|false` (case-insensitive) plus optional spaces or TABs. Each of these is still
   rejected: any other value, `None or <v>`, `None <v>`, `None, <v>`, `None; <v>`,
   `Nonesense<v>`, a literal `\n` after None, `true<v>`, `== <v>`, `<REDACTED>`, a second
   assignment on the same line, or `Bearer <v>` after None. The final JSON-escaped gate gets the
   same exemption (escaped `\n`/`\t`) via `_SENSITIVE_JSON`. The TAB + `oken = <v>` join is still rejected.
   No other credential pattern changed: PEM, AKIA/ASIA, `sk-`, GitHub, Slack, bearer/basic, JWT and URL userinfo.
2. **Logger lines.** In unittest, the existing `ERROR:root:` exemption now covers any
   `ERROR:<logger>:` line. A separator-anchored one is still `malformed_failure_header`.
   In pytest, `ERROR:<logger>:` and the captured-log `ERROR    <logger>:file:line` lines are skipped
   **only before the short-summary banner**, where no failure ID can be counted.
   After the banner they stay malformed. A forged `ERROR tests/…::t` or `FAILED …` before the banner still leaves the result INCOMPLETE.

## Remaining false outcomes (not fixed: tradeoffs)

- `Basic auth disabled` → REJECTED (both frameworks). `basic\s+\S+` cannot tell the English word
  `auth` from a base64 credential (`auth` is valid base64, and short Basic credentials exist).
  Any narrowing (minimum length, padding, alphabet) would weaken detection. Kept fail-closed.
- unittest `-v` with logging interleaved → INCOMPLETE. A standalone `FAIL`/`ERROR` status token is
  ambiguous. Kept (same decision as #186).
- unittest with block-buffered stdout (no `-u`, `2>&1` capture) → INCOMPLETE `nonterminal_summary`.
  Test prints flush after the summary. Recommend capturing with `python -u` / `PYTHONUNBUFFERED=1`.
- pytest capture where a test prints a bare `OK` line → INCOMPLETE `mixed_formats`.
- `token = <REDACTED>` (and other redaction markers) stays REJECTED. Not exempted, to keep the change minimal.

## Fidelity coverage added (item 3)

- Real unittest captures from a throwaway temp-dir test file, including 48 KiB+ outputs.
  The real pytest run remains in the existing test and is skipped when pytest is absent.
- Real processes, no mocks:
  - The reader closes the pipe after 8 KiB of a flood. The child exits nonzero, and the partial capture is INCOMPLETE/FAILED with no IDs.
  - SIGTERM mid-run gives exit −15 → INCOMPLETE/FAILED.
  - Real failures with a forced exit 0 → `exit_summary_mismatch`.
  - Python ≥3.12 `NO TESTS RAN` exit 5 → INCOMPLETE.
  - A passing run with exit 0 → COMPLETE/`NO_FAILURES_REPORTED`, and the same capture with exit 1 → INCOMPLETE.
- Near-1 MiB adversarial JWT shapes in a child interpreter with a 5 s bound:
  - Rejected: short-segment chain then a JWT; KELVIN SIGN segments; a combining mark after the tail.
  - INCOMPLETE: broken adjacency; two-segment `ı` chains; a huge dash segment.
  - The new exemption's lookahead also has near-limit linearity cases.
  - Observed 0.15–0.87 s per case on the dev Mac.
- Every case asserts the capture's bytes/mtime/inode/mode are unchanged. Each also asserts that the output and errors never echo the capture path or the synthetic private marker.
