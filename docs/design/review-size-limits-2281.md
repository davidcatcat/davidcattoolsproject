# Investigation note: review size limits (Issue #2281, limit-table row 12)

Date of this read-only investigation: prepared from the read scope
(`tools/llmtool/llmtool/task_review.py`, `tools/llmtool/llmtool/repair_policy.py`,
`tools/llmtool/policy.json`) for planning the Owner decision "every #2281 limit
table limit becomes configurable". No code or test change was made.

Scope rule: callers outside the read scope are out of scope for this note and
are said so explicitly where relevant. Precedent note PR #2336 (EC2 module
limit investigation) is cited from the task contract; its file is outside this
read scope, so nothing below is asserted from its contents.

## 1. Where the row-12 review size limits are set

Row 12 of the #2281 limit table records: review packet 256 KiB, review source
128 KiB, Claude review envelope 128 KiB. In `tools/llmtool/llmtool/task_review.py`
they are module-level constants:

- `PACKET_LIMIT = 256 * 1024` — the review packet (256 KiB), a literal
  multiple; no policy key exists for it.
- `SOURCE_LIMIT = 128 * 1024` — review diff + selected sources (128 KiB).
- `VERDICT_LIMIT = 64 * 1024` — the bound for parsed verdict JSON (64 KiB).
  Row 12 names the Claude review *envelope* 128 KiB, which is *not* one of
  these constants; the 128 KiB envelope figure lives only in the issue's limit
  table. The closest in-scope analogs are `VERDICT_LIMIT` and the inline
  literal `4096` limit on launcher metadata JSON in `run_review`.
  Flagged here as a spec-vs-code observation: the note cannot resolve which
  bound row 12's envelope row refers to from inside the read scope.
- Related, same family (set but not row-12 items): `EVIDENCE_LIMIT = 32 * 1024`
  (evidence + CI section) and the inline literals `1024 * 1024` (fixture
  packet input parsing in `run_review`) and `4096` (launcher
  `metadata_json` / `job_evidence_json` parsing in `run_review`).

None of these constants is read from `tools/llmtool/policy.json` and none
appears in `repair_policy.py`; `tools/llmtool/policy.json` contains no review
size key. The values in `tools/llmtool/policy.json` (for example
`spec_preflight_large_file_lines`) are the merged #2281 keys only.

## 2. Where they are enforced and what happens when input exceeds a limit

Enforcement is entirely in `tools/llmtool/llmtool/task_review.py`:

- `ReviewPacket.__post_init__` (dataclass `ReviewPacket`): refuses with
  `reject('REVIEW_PACKET_TOO_LARGE', component='total', limit=PACKET_LIMIT)`
  when the packet byte string exceeds `PACKET_LIMIT`, then re-checks the same
  bound through `load_json`.
- `make_packet`: refuses `REVIEW_PACKET_TOO_LARGE` three more ways:
  - `component='contract'`, limit `VERDICT_LIMIT`, when either task contract's
    JSON encoding exceeds `VERDICT_LIMIT`;
  - `component='diff_source'`, limit `SOURCE_LIMIT`, when diff bytes plus
    selected-source bytes exceed `SOURCE_LIMIT`;
  - `component='test_ci'`, limit `EVIDENCE_LIMIT`, when the canonicalized
    evidence + CI section exceeds `EVIDENCE_LIMIT`;
  - plus the whole-payload `component='total'`, limit `PACKET_LIMIT` check on
    the canonical payload just before building the `ReviewPacket`.
- `load_json` (module function): bound and nesting check *before* JSON
  parsing; the default `limit` argument is `VERDICT_LIMIT`, so every
  unbounded call site (notably the verdict text in `_parse_verdict`) is
  limited to 64 KiB, with nesting refused above 16.
- `run_review` (module function): passes inline literals as limits —
  `load_json(fixture.packet_input.decode(), 1024*1024)` for the fixture
  packet input and `load_json(..., 4096)` for the launcher metadata and job
  evidence JSON.

What happens when a review input is over a limit: `reject(code, *, component,
limit)` raises a `coded_error('review rejected; details omitted',
'USER_INPUT_ERROR', 'input')` that carries `review_code` and
`review_metadata` (`component`, `limit_bytes`, suggestion `split scoped PR`)
only when `component` is given. `run_review` catches it and returns the
envelope from `envelope(...)` with `code` from `review_code`,
`limit_metadata` from `review_metadata`, status `BLOCKED`,
`authorized` false and `invocation_status` preserved. So an oversized review
input fails closed: the review is refused, the Task is not approved, the
provider is not launched for input-side rejections, and the CLI receives a
diagnostic naming the component and limit bytes with the suggestion to split
the scoped PR. Over-limit verdict text likewise fails closed through
`parse_verdict` (`REVIEW_VERDICT_INVALID`).

## 3. How the already merged #2281 policy keys are loaded and validated

All in `tools/llmtool/llmtool/repair_policy.py` plus the data file
`tools/llmtool/policy.json`:

- Key registry: `POLICY_KEYS` (required), `OPTIONAL_POLICY_LIMITS`
  (`timeout_continuation_max`, bounded 0..3), and
  `OPTIONAL_POSITIVE_POLICY_KEYS` — the #2281 L0b batch, each a positive
  integer with no upper validation bound; an absent key keeps its caller
  default, and a present key of the wrong type, zero or negative refuses
  `POLICY_INVALID`. The comment block lists their #2281 limit-table rows
  (row 2, rows 15-17).
- File location: `policy_path()` resolves the source `policy.json`, then a
  module-local copy, then the installed data directory; it never creates a
  file.
- Read bound: `load_policy` is #2281 L0a (design rule 3) — the bound is a
  real technical limit, not an arbitrary cap: it reads at most `_MAX_BYTES + 1`
  bytes (`_MAX_BYTES = 16 * 1024`, 16 KiB) in one bounded buffer and refuses
  on any overflow, so the read buffer itself is bounded.
- Validation: strict whitelist — `schema_version` must be exactly `1`; every
  `POLICY_KEYS` entry an int in 0..1000; optional limits within their
  registered (min, max); positive keys `>= 1`; any unknown key, wrong type,
  non-int or out-of-range value refuses `POLICY_INVALID` via `_refuse`.
  `policy.json` is treated as untrusted input ("any doubt refuses").
- Current `tools/llmtool/policy.json` content: `schema_version` 1; the
  required `POLICY_KEYS` (`repair_max` 5, `parent_debug_max` 1,
  `group_debug_max` 3, `child_max` 3, `replan_max` 1, `test_fail_max` 5,
  `review_fail_max` 6, `waiting_notify_after_hours` 24); `timeout_continuation_max`
  1; and the #2281 keys `spec_preflight_large_file_lines` 2000,
  `merge_train_members_max` 6, `merge_train_files_per_pr_max` 100,
  `revert_scope_prs_max` 5, `d6_writer_lease_hours` 24.
- Wiring: callers receive numbers from `load_policy`; `repair_attempts_max`
  shows the read path and fails closed (returns 0) when policy cannot be
  read. Out-of-scope note: the `repair_policy.py` comment says #2281 L0b
  callers are "wired in later pieces", and files outside the read scope
  (including `scripts/d6_worktree_lifecycle.py`, named in the `d6_writer_lease_hours`
  comment) cannot be verified from this note.

Contradiction check against the goal's framing: row 12's three numbers do
appear as AI-set module constants in `task_review.py` with no recorded basis
(no policy key, no test-visible default), so the stated problem matches the
code as read; no SPEC_MISMATCH is triggered by the in-scope files. The one
caveat is the "Claude review envelope 128 KiB" item: the in-scope code has
`VERDICT_LIMIT = 64 * 1024` and 4096-byte launcher metadata bounds, but no
128 KiB envelope constant, so that single row may refer to a bound set
outside the read scope (launcher/provider side) or to a planning value.

## 4. Options for making the row-12 limits configurable (under #2281 design rules)

Design rules already in force in scope (from `repair_policy.py` comments and
implementation, #2281 L0a/L0b): policy.json is the only store; any limit is a
real technical limit, not an arbitrary cap (L0a); optional keys are whitelisted
with type/range validation, fail closed on doubt, and an absent key keeps its
caller default (L0b). At most three options:

1. **Register the row-12 keys in `policy.json` and read them in `task_review.py`**
   (direct L0b pattern). Add, for example, `review_packet_limit_bytes`
   (default 262144), `review_source_limit_bytes` (default 131072),
   `review_verdict_limit_bytes` (default 65536) to
   `OPTIONAL_POSITIVE_POLICY_KEYS` in `repair_policy.py`, list them in
   `tools/llmtool/policy.json`, and have `task_review.py` resolve each
   constant through `load_policy` at call time (fail-closed default when the
   policy cannot be read, matching `repair_attempts_max`).
   Limits of the option: `load_policy` reads a bounded 16 KiB buffer and the
   file is untrusted input, so every new key adds validation burden; the
   `task_review.py` constants are module-level and used in dataclass
   constructors (`ReviewPacket.__post_init__`), so a default policy must be
   passed or re-resolved at every enforcement site; byte-size keys need their
   own range validation (positive integers, and sanity upper bounds), unlike
   the dimensionless counts in `POLICY_KEYS`; it also widens the
   `policy.json` schema and every installed-copy deployment path handled by
   `policy_path`.
   Files it would touch: `tools/llmtool/policy.json`,
   `tools/llmtool/llmtool/repair_policy.py`,
   `tools/llmtool/llmtool/task_review.py`.

2. **Keep the constants as the sole defaults; make them policy-overridable only
   at the packet-construction boundary** (`make_packet` / `ReviewPacket`).
   `task_review.py` keeps `PACKET_LIMIT`, `SOURCE_LIMIT`, `EVIDENCE_LIMIT`,
   `VERDICT_LIMIT` as documented defaults; `make_packet` takes an optional
   policy-derived limits mapping, validated by new pure helpers in
   `repair_policy.py`, and refuses `POLICY_INVALID` on a mismatched or
   non-positive value. Row-12 compliance is proven by the default values;
   the #2281 L0a "real technical limit" rule is satisfied because the bounds
   remain the shipped behavior unless the host explicitly overrides them.
   Limits of the option: the enforcement sites outside `make_packet`
   (`load_json` default, `_parse_verdict`, the inline literals in
   `run_review`) would still hard-code their bounds, so configurability would
   be partial and split across two mechanisms; any caller (out of scope here)
   that constructs a `ReviewPacket` directly bypasses the override path
   unless the dataclass also takes the limits, which changes a public
   constructor.
   Files it would touch: `tools/llmtool/llmtool/task_review.py`,
   `tools/llmtool/llmtool/repair_policy.py` (helpers only; no new
   `policy.json` key required if overrides are host-injected, though that
   reintroduces the same host-trust question the L0b design answers with
   policy.json).

3. **Defer row 12 to a later L0b-style batch**, reusing the exact merged
   machinery without new design. Record the row-12 constants as
   "AI-set, no recorded basis" in the issue until their real technical
   rationale (L0a) is documented, then land them in
   `OPTIONAL_POSITIVE_POLICY_KEYS` in a follow-up piece exactly as the
   currently merged keys were landed ("callers are wired in later pieces",
   per the `repair_policy.py` L0b comment). Limits of the option: it is the
   slowest path and leaves the row-12 numbers non-configurable in the
   meantime; it also inherits the open question about which bound the 128 KiB
   "Claude review envelope" row refers to (section 3 caveat), so the follow-up
   may need to touch files outside this read scope (launcher/provider side),
   which this note cannot plan.
   Files it would touch: `tools/llmtool/policy.json`,
   `tools/llmtool/llmtool/repair_policy.py`,
   `tools/llmtool/llmtool/task_review.py` (later piece, same as option 1);
   possibly out-of-scope launcher files for the envelope row.

No option is recommended: the decision is the Owner's, and per the task
contract this note recommends none.

## 5. Test-data note (Owner #763 A)

This is a read-only note; no tests are added or modified, so the #763 A
rule is not exercised. For the follow-up it matters: any refusal-flow test
for over-limit review input must mock, monkeypatch or inject the screen/pack
function and still check the real `reject`/`envelope` behaviour, and must
never embed credential-shaped strings in ordinary test data.

## 6. What this note cannot say (out of read scope)

- Callers of `task_review.py` and of `repair_policy.load_policy` outside the
  three in-scope files (CLI entry points, `scripts/d6_worktree_lifecycle.py`,
  the review launcher/provider, and the EC2 module behind precedent PR #2336)
  are out of scope: where those callers pass or read limits is not asserted
  here.
- The PR #2336 precedent note file is not in the read scope; it is cited only
  as the contract's named precedent.
- No credentials, personal data or network access were used to write this
  note.
