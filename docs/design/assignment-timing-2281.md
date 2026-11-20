# Developer assignment wait and expiry limits (Issue #2281 row 18): where they live, where they are enforced, and how #2281 policy keys are loaded

Read-only investigation note for Owner follow-up on Issue #2281 (davidcattoolsproject),
limit table row 18 (Developer assignment wait 540 s; expiry 600 s; expiry at most 900 s).
Precedent: PR #2336 (EC2 module limit investigation note).

Scope and method. This note covers exactly the read scope:
`tools/llmtool/llmtool/developer_assignment_inbox.py`, `tools/llmtool/llmtool/repair_policy.py`,
`tools/llmtool/policy.json`. Code references name the file and the function or constant;
no line numbers. Callers and modules outside the read scope (`task_store`, `task_completion`,
`worker_dispatch`, `active_pm_inbox`, `role_config`, `errors`, and any CLI layer) are out of
scope for this note and are marked as such wherever they matter. No code or test change is
made by this note. Every factual statement below names its repository file and the function
or constant it comes from.

## Q1: Where are the assignment wait and expiry limits set?

All three row-18 numbers are module-level constants in
`tools/llmtool/llmtool/developer_assignment_inbox.py`:

- `DEFAULT_WAIT_SECONDS = 540` — the row-18 "wait" limit; the default bounded wait of the
  developer's `task assignments --worker <name>` command.
- `DEFAULT_EXPIRY_SECONDS = 600` — the row-18 "expiry" limit; the default an assignment
  stays claimable after it is recorded as ASSIGNED.
- `ASSIGNMENT_EXPIRY_MAX_SECONDS` — the row-18 "expiry at most" cap. It is not defined in
  `tools/llmtool/llmtool/developer_assignment_inbox.py`; the module imports it from the
  `.task_store` module (`from .task_store import ...` at the top of the file). The task
  store module is outside the read scope, so its exact value (900 s per the Issue) is not
  verified by this note; it is the only row-18 limit whose definition sits outside the
  scope. The module docstring of `tools/llmtool/llmtool/developer_assignment_inbox.py`
  (assignment "expiring within 15 minutes") is consistent with a 900 s cap.
- Related timing constant in the same file: `POLL_SECONDS = 2.0`, the sleep-poll interval
  used by `wait_assignments`; it is not one of the row-18 limits.

Where the defaults feed in:

- `tools/llmtool/llmtool/developer_assignment_inbox.py` / `assign()`: its parameter
  `expires_in` defaults to `DEFAULT_EXPIRY_SECONDS`; the stored ASSIGNED record's
  `expires_at` is `assigned_at + expires_in`.
- `tools/llmtool/llmtool/developer_assignment_inbox.py` / `wait_assignments()`: its
  parameter `timeout` defaults to `DEFAULT_WAIT_SECONDS`.

No part of the read scope, including `tools/llmtool/policy.json`, reads or holds any of
these three values; today nothing wires them to the #2281 policy.

## Q2: Where are the limits enforced?

Enforcement lives in `tools/llmtool/llmtool/developer_assignment_inbox.py`:

1. `assign()` — the expiry limit is enforced at assignment time. It requires
   `type(expires_in) is int` and `0 < expires_in <= ASSIGNMENT_EXPIRY_MAX_SECONDS`, and
   otherwise raises `AssignmentRefused` with refusal code `ASSIGNMENT_EXPIRY_INVALID`
   before anything is written. A caller that passes an out-of-cap expiry is refused; the
   cap itself comes from the out-of-scope `.task_store` import.
2. `wait_assignments()` — the wait limit is enforced at call time. It requires
   `type(timeout) is int` and `0 <= timeout <= MAX_WAIT_SECONDS`, and otherwise raises
   `AssignmentRefused` with code `ASSIGNMENT_WAIT_TIMEOUT_INVALID`. `MAX_WAIT_SECONDS`
   (600) is the local ceiling on how long the developer may sleep-poll, checked before
   any polling loop starts.
3. Expiry against the clock — `_live()` is the only place in the read scope where the
   stored `expires_at` is compared with the current time: an item is live only while
   `state == 'ASSIGNED'` and `now < expires_at`. `_live()` is applied by
   `pending_assignments()` (developer view, used by `wait_assignments()`) and by
   `pm_assignments()` (PM inbox view; an expired-but-unclaimed item shows as UNCLAIMED).
   Expiry is therefore evaluated lazily at list time; nothing in the read scope moves a
   row to EXPIRED by itself.
4. `claim()` — the visible gate in the read scope re-checks dispatch, the D6 registration
   (root and `source_sha` unchanged), and the session; the stored `expires_at` is not
   re-checked inside this file's `gate()`. Per the module docstring, a late claim moves
   the assignment to EXPIRED, which happens in the out-of-scope `.task_store` store
   (`store.claim_developer_assignment` is called on that store). The authoritative
   late-claim check against `expires_at` therefore lives in the store, outside the read
   scope; the only in-scope evidence is the docstring statement above.
5. `tools/llmtool/llmtool/repair_policy.py` enforces none of the row-18 limits; its
   limits are the review/repair/debug/plan limits documented in its module docstring.

## Q3: How are the already merged #2281 policy keys loaded and validated?

Loading and validation live in `tools/llmtool/llmtool/repair_policy.py`:

- `POLICY_KEYS` — the eight required #1502 limits (`repair_max`, `parent_debug_max`,
  `group_debug_max`, `child_max`, `replan_max`, `test_fail_max`, `review_fail_max`,
  `waiting_notify_after_hours`).
- `OPTIONAL_POLICY_LIMITS` — the #1815 T3 optional range limit
  (`timeout_continuation_max`, bounds 0..3).
- `OPTIONAL_POSITIVE_POLICY_KEYS` — the merged #2281 AI-set keys from the Issue's limit
  table: row 2 `spec_preflight_large_file_lines`, row 15 `merge_train_members_max` and
  `merge_train_files_per_pr_max`, row 16 `revert_scope_prs_max`, and row 17
  `d6_writer_lease_hours`. The constant's comment says: each is a positive integer (>= 1)
  with no upper validation bound; an absent key keeps its caller default; a present key
  of the wrong type, zero or negative refuses `POLICY_INVALID`; and callers are wired in
  later pieces.
- `policy_path()` — resolves which `tools/llmtool/policy.json` to read: source tree,
  module directory, then an installed copy, first existing one wins.
- `load_policy(path=None)` — reads at most `_MAX_BYTES + 1` bytes in one bounded buffer
  (16 KiB; #2281 L0a, design rule 3: the bound is a real technical limit and must stay
  above the largest possible valid policy so it can never refuse a valid one) and
  strictly validates: top level must be a dict; `schema_version` must be int 1; every
  `POLICY_KEYS` key must be present as an int in 0..1000; the #1815 optional key, if
  present, must be an int in 0..3; each #2281 positive key, if present, must be an int
  >= 1 (no upper bound); and no key outside the allowed set is admitted. Any doubt
  refuses `POLICY_INVALID` (`_refuse()`).
- `repair_attempts_max(path=None)` — shows the fail-closed read pattern used by merged
  consumers: an unreadable policy gives 0.

`tools/llmtool/policy.json` today carries `schema_version: 1`, all eight `POLICY_KEYS`,
`timeout_continuation_max`, and the five #2281 keys (2000, 6, 100, 5, 24 respectively). It
contains no assignment wait or expiry key — consistent with row 18 being listed in the
Issue with no recorded basis and with the `OPTIONAL_POSITIVE_POLICY_KEYS` comment that
callers are wired in later pieces.

Note on scope for the validation question: which merged callers consume the #2281 keys is
not visible inside the read scope (`tools/llmtool/llmtool/repair_policy.py`'s
`load_policy`/`OPTIONAL_POSITIVE_POLICY_KEYS` are the loader and the key set; the
consumers the comments name, e.g. the #2281 L7 reader `scripts/d6_worktree_lifecycle.py`,
are outside the read scope and are not asserted by this note).

## Options for making the row-18 limits configurable under the #2281 design rules

Per the #2281 design rules as applied by `tools/llmtool/llmtool/repair_policy.py`:
policy.json is untrusted input read by bounded read, limits are validated at load time
with `POLICY_INVALID` refusal, absent keys keep the caller default, and an AI-set limit
that exists has no upper validation bound (`OPTIONAL_POSITIVE_POLICY_KEYS`). Three options:

1. Extend the positive-integer mechanism with an upper bound on the expiry cap. Add
   `assignment_wait_seconds` and `assignment_expiry_seconds` to
   `tools/llmtool/llmtool/repair_policy.py` as a new option group (positive int, absent
   keeps the caller default, wrong type/zero/negative refuses `POLICY_INVALID`), and add
   `assignment_expiry_max_seconds` with an explicit upper bound (900) so the invariant
   "expiry at most" is still machine-checked — unlike the existing
   `OPTIONAL_POSITIVE_POLICY_KEYS`, which are positive with no upper validation bound, so
   a separate key group is needed. `tools/llmtool/llmtool/developer_assignment_inbox.py`
   would resolve its wait default and expiry default from `load_policy()` (absent key
   keeps the current defaults), stop importing the cap from `.task_store` (the store
   constant would be removed or re-sourced) and enforce the cap from the policy value.
   Limitations: policy.json is out-of-scope for writers, so a bound is an additional,
   new validation shape; every assignment-inbox caller outside the read scope that
   relies on the current hard defaults or on the store's cap would need the later
   wiring; and a policy that cannot be read fails closed to the current defaults only if
   the code chooses that fallback, which the #2281 fail-closed precedent
   (`repair_attempts_max` gives 0) does not obviously dictate for a timing default.
   Files it would touch: `tools/llmtool/policy.json`,
   `tools/llmtool/llmtool/repair_policy.py`,
   `tools/llmtool/llmtool/developer_assignment_inbox.py`, and the out-of-scope
   `task_store` (cap constant) plus its callers, which are not investigated here.
2. Keep the hard cap and make only the two defaults policy-configurable. Add
   `assignment_wait_seconds` and `assignment_expiry_seconds` to
   `OPTIONAL_POSITIVE_POLICY_KEYS` in `tools/llmtool/llmtool/repair_policy.py` (no upper
   bound, following the row-2..17 precedent), leave the "at most 900 s" cap as the
   hard-wired `ASSIGNMENT_EXPIRY_MAX_SECONDS` in the out-of-scope `task_store` import of
   `tools/llmtool/llmtool/developer_assignment_inbox.py`, and have `assign()` and
   `wait_assignments()` read the policy with absent-key defaults. Limitations: this
   inherits the merged #2281 behaviour that a present key has no upper validation bound,
   so a policy could hold an expiry larger than the cap and it would be refused only
   later, at `assign()` time with `ASSIGNMENT_EXPIRY_INVALID`, not at load time with
   `POLICY_INVALID`; the "at most" limit stays a code constant with no recorded basis in
   the policy file (the very gap row 18 complains about, half solved); and the wait cap
   `MAX_WAIT_SECONDS` would remain hard-wired unless folded into the same policy read.
   Files it would touch: `tools/llmtool/policy.json`,
   `tools/llmtool/llmtool/repair_policy.py`,
   `tools/llmtool/llmtool/developer_assignment_inbox.py`; the `task_store` cap stays as
   is.
3. Move the three numbers fully into policy with a policy-level invariant check. Add all
   three keys (`assignment_wait_seconds`, `assignment_expiry_seconds`,
   `assignment_expiry_max_seconds`) and validate, in `load_policy()` in
   `tools/llmtool/llmtool/repair_policy.py`, that all are positive ints, that the expiry
   default is at most the expiry cap, and that the wait default is at most the expiry
   cap (the developer cannot wait longer than an assignment can stay live).
   `tools/llmtool/llmtool/developer_assignment_inbox.py` would take all three from the
   policy (absent keys keep today's 540/600/900) and `MAX_WAIT_SECONDS` would be dropped
   as a local constant in favour of the policy-derived cap. Limitations: the strongest
   change to the merged #2281 validation shape — `load_policy()` has never validated a
   relation between keys, only per-key ranges — so a new cross-key rule would be novel to
   the module's fail-closed style and must be kept consistent with `_MAX_BYTES` sizing;
   and it still requires the out-of-scope `task_store` cap constant to be removed or
   re-sourced, plus wiring in every assignment-inbox caller outside the read scope, which
   this note cannot enumerate. Files it would touch: `tools/llmtool/policy.json`,
   `tools/llmtool/llmtool/repair_policy.py`,
   `tools/llmtool/llmtool/developer_assignment_inbox.py`, and the out-of-scope
   `task_store`.

Recommendation: none of the options is recommended as decided. The Owner decided every
such limit becomes configurable; the choice among these options is left to that follow-up
planning.

## Out of scope

- `tools/llmtool/llmtool/task_store.py` (`ASSIGNMENT_EXPIRY_MAX_SECONDS`,
  `record_developer_assignment`, `claim_developer_assignment`, the late-claim EXPIRED
  transition) — only its names and the import in
  `tools/llmtool/llmtool/developer_assignment_inbox.py` are visible here.
- `worker_dispatch`, `task_completion`, `active_pm_inbox`, `role_config`, `errors` —
  called from `tools/llmtool/llmtool/developer_assignment_inbox.py` but not in the read
  scope.
- The CLI layer that parses `--wait`/`--timeout` and the assignment duration flags, and
  any caller outside the read scope of the #2281 policy keys.
- The Issue #2281 page and the PR #2336 note are cited as the task's evidence; their
  contents are not re-verified here beyond the row-18 statement in the task.
