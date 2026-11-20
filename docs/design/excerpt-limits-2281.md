# Excerpt limits of Issue #2281 (row 19): set, enforced, and #2281 key loading

Read-only investigation note for Issue #2281 row 19, which records the Reviewer,
AI Debug and CI excerpt limits (2000 chars, 6000 chars, 40 lines) as AI-set with no
recorded basis. Precedent note: PR #2336 (EC2 module limit investigation). No code
or test change is made; this file is the only one written.

## Scope

- Read scope of this note: `tools/llmtool/llmtool/repair_policy.py`,
  `tools/llmtool/llmtool/start_failure.py`, `tools/llmtool/policy.json`.
- Everything else is out of scope, including the repair-packet writer/validator
  (named in `tools/llmtool/llmtool/start_failure.py` docstrings as
  `task_submission_cli._validate_repair_packet`), the CI-excerpt writer (named as
  `review_repair._ci_failure`) and the task store (named as
  `task_store.REPAIR_PACKET_MAX_BYTES`). No statement below is a fact about files
  outside the read scope.

## Q1 — Where the limits are set

- `tools/llmtool/llmtool/start_failure.py`:
  - `REVIEWER_EXCERPT_MAX_CHARS = 2000` (module constant): character bound for the
    Reviewer excerpt field `reviewer_text_excerpt_untrusted_data` of a start-failure
    repair packet.
  - `HIGH_DEBUG_EXCERPT_MAX_CHARS = 6000` (module constant): character bound for the
    AI Debug (High Debug) report field `high_debug_report_untrusted_data`.
  - `_CI_EXCERPT_PREFIX` (module constant, `CI_CHECK_FAILURE `): identifies a CI
    excerpt by its first line; it is a prefix marker, not a length or line limit.
- `tools/llmtool/policy.json`: no excerpt keys. Its keys are `schema_version` (1),
  the eight `POLICY_KEYS` limits, `timeout_continuation_max`, and the five #2281 keys
  named in `OPTIONAL_POSITIVE_POLICY_KEYS` in
  `tools/llmtool/llmtool/repair_policy.py`.
- The row-19 40-line CI-excerpt limit is not present in the read scope: neither
  `tools/llmtool/llmtool/start_failure.py` nor
  `tools/llmtool/llmtool/repair_policy.py` contains a line-count limit, and
  `tools/llmtool/policy.json` has no line-count key. Where the 40-line limit is set
  and applied is outside the read scope (the CI-excerpt writer named in
  `tools/llmtool/llmtool/start_failure.py` is `review_repair._ci_failure`, out of
  scope).

## Q2 — Where they are enforced, and what happens to text over a limit

All in `tools/llmtool/llmtool/start_failure.py`:

- `repair_packet_shape_ok` admits a packet only while
  `len(reviewer_text_excerpt_untrusted_data) <= REVIEWER_EXCERPT_MAX_CHARS` and, when
  the High Debug fields are present,
  `len(high_debug_report_untrusted_data) <= HIGH_DEBUG_EXCERPT_MAX_CHARS`. Over-limit
  text makes the packet invalid: the function returns `False`. It does not truncate,
  mark or return an error message at this point.
- `decode_repair_packet` returns `None` for stored packet text that is not ASCII, not
  strict JSON, has duplicate keys, fails `repair_packet_shape_ok`, or is not exactly
  the canonical encoding of the dict. An over-limit excerpt therefore makes a stored
  packet undecodable.
- `repair_packet_matches` → `_matches` requires `repair_packet_shape_ok(packet)` as
  the first condition, so an over-limit packet never binds to its claim;
  `repair_packet_matches` returns `False`, and its docstring records that the caller
  then refuses `START_RETRY_REPAIR_PACKET_MISMATCH`. That caller is outside the read
  scope.
- The only truncation in the read scope is at binding time: `_matches` requires the
  packet's `high_debug_report_untrusted_data` to equal the first
  `HIGH_DEBUG_EXCERPT_MAX_CHARS` characters of the `report` recorded on the root
  Task. A recorded RCA report longer than 6000 characters can therefore only round
  trip as its first 6000 characters; any other encoding fails the match.
- The CI excerpt is not length-limited in the read scope: `_ci_identity_sha256` reads
  only the first line of `reviewer_text_excerpt_untrusted_data` (fullmatch of the CI
  header) and `_matches` routes to that identity digest when the excerpt starts with
  `_CI_EXCERPT_PREFIX`; lines after the first are ignored by the digest.

## Q3 — How the merged #2281 policy keys are loaded and validated

All in `tools/llmtool/llmtool/repair_policy.py`:

- `policy_path` resolves the file: the source `tools/llmtool/policy.json` first, then
  a module-directory copy, then the packaged/installed copy.
- `load_policy` reads at most `_MAX_BYTES` (16 KiB) + 1 byte in one bounded buffer
  (design rule L0a: `policy.json` is untrusted input) and refuses
  `POLICY_INVALID` (via `_refuse`, which raises `LLMToolError`) on oversize, read,
  decode or JSON error.
- `load_policy` validation (all-or-nothing; any doubt refuses `POLICY_INVALID`):
  - top level is a dict and `schema_version` is an int equal to 1;
  - all eight `POLICY_KEYS` are present, each an int in 0..1000;
  - no key outside the allow-list of `schema_version`, the `POLICY_KEYS`,
    `OPTIONAL_POLICY_LIMITS` and `OPTIONAL_POSITIVE_POLICY_KEYS`;
  - `OPTIONAL_POLICY_LIMITS` (`timeout_continuation_max`): when present, an int in
    [0, 3];
  - `OPTIONAL_POSITIVE_POLICY_KEYS` — the merged #2281 keys
    (`spec_preflight_large_file_lines`, `merge_train_members_max`,
    `merge_train_files_per_pr_max`, `revert_scope_prs_max`, `d6_writer_lease_hours`):
    each optional; when present it must be an int and at least 1, with no upper
    validation bound (design rule L0b); a wrong type, zero or negative value refuses
    `POLICY_INVALID`; an absent key keeps its caller default.
- `load_policy` returns a copy of the dict. Fail-closed consumption is shown by
  `repair_attempts_max`, which returns 0 (no repair admitted) when `load_policy`
  refuses.
- `tools/llmtool/policy.json` currently sets the five #2281 keys to 2000 / 6 / 100 /
  5 / 24.
- No reader of the #2281 keys lives in the read scope: the
  `OPTIONAL_POSITIVE_POLICY_KEYS` comment in
  `tools/llmtool/llmtool/repair_policy.py` records that callers are wired in later
  pieces (the one named reader is `scripts/d6_worktree_lifecycle.py` for
  `d6_writer_lease_hours`, out of scope).
- Design rules recorded in the comments of `tools/llmtool/llmtool/repair_policy.py`:
  - L0a: a bound is a real technical limit, not an arbitrary cap; the `policy.json`
    read is bounded by `_MAX_BYTES` because the file is untrusted input.
  - L0b: an AI-set limit from the #2281 limit table is a positive integer (>= 1) with
    no upper validation bound; an absent key keeps its caller default.
- Row-19 status: the excerpt limits are not among the #2281 keys and
  `tools/llmtool/policy.json` cannot express them today; they are hard-coded
  constants in `tools/llmtool/llmtool/start_failure.py`.

## Options for making the row-19 limits configurable (none recommended)

The Owner decided every such limit becomes configurable; this note lists options only
and recommends none.

1. **Add row-19 keys to `OPTIONAL_POSITIVE_POLICY_KEYS`** (existing L0b pattern).
   - Files: `tools/llmtool/llmtool/repair_policy.py` (extend the tuple and the
     row-19 comment), `tools/llmtool/policy.json` (add the keys, e.g. at the current
     2000 and 6000), `tools/llmtool/llmtool/start_failure.py` (replace the module
     constants with policy-driven limits in `repair_packet_shape_ok`, `_matches`, and
     their callers `decode_repair_packet` / `repair_packet_matches`).
   - Limits: L0b has no upper validation bound and the read scope shows no technical
     ceiling (L0a basis) for a character count; the packet text is a stored
     cross-run contract, so the writer
     (`task_submission_cli._validate_repair_packet`, out of scope) and the reader must
     adopt a changed bound together, or old stored packets stop decoding/matching
     (tighter value) or over-limit packets pass (looser value) until the writer is
     updated; `repair_packet_shape_ok` is pure and takes only the packet dict, so a
     policy-driven limit changes its call shape and ripples to the out-of-scope
     callers.
2. **A new bounded optional-limits entry in `repair_policy.py`** (the
   `OPTIONAL_POLICY_LIMITS` pattern, e.g. each char bound in [1, N]).
   - Files: `tools/llmtool/llmtool/repair_policy.py` (new entry plus its validation
     branch in `load_policy`), `tools/llmtool/policy.json`,
     `tools/llmtool/llmtool/start_failure.py`.
   - Limits: deviates from the recorded L0b rule (no upper bound) for #2281-table
     keys, so the Owner must approve a new validation rule for character limits; the
     upper bound N must satisfy L0a as a real technical limit, and no candidate is
     visible in the read scope; the same caller/signature ripple as option 1.
3. **Caller-passed limits instead of module constants** (an explicit limits argument
   on the shape/decode/match functions defaulting to the current 2000/6000; values
   can later be sourced from `policy.json` or elsewhere).
   - Files: `tools/llmtool/llmtool/start_failure.py` (signatures and defaults) plus
     every out-of-scope caller of those functions — an unbounded ripple that must be
     inventoried separately.
   - Limits: no `policy.json` change; the value only moves location, so
     external configurability (e.g. via `policy.json`) is not achieved; signature
     changes break the out-of-scope callers.

## Out-of-scope statements

- The 40-line CI-excerpt limit: not settable from the read scope; its writer and
  enforcement site (the named `review_repair._ci_failure` and
  `task_submission_cli._validate_repair_packet`) are outside the read scope.
- The callers of the #2281 keys (e.g. `scripts/d6_worktree_lifecycle.py`) are outside
  the read scope; this note records only how the keys load and validate.
- The store byte bound `task_store.REPAIR_PACKET_MAX_BYTES` named in the
  `decode_repair_packet` docstring of `tools/llmtool/llmtool/start_failure.py` is
  outside the read scope.
