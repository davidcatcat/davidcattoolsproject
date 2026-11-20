# Waiting notice delay (Issue #2281, limit table row 21): fact record

Scope of this note: only the files `tools/llmtool/llmtool/repair_policy.py` and
`tools/llmtool/policy.json`. Every other location of this key or of the WAITING
notice flow is outside the read scope.

Row 21 of the Issue #2281 limit table
(https://github.com/davidcatcat/davidcattoolsproject/issues/2281) records the
Owner notice delay after a WAITING state as 24 hours, configurable under the
policy key `waiting_notify_after_hours`, with no recorded basis. The three
facts below answer where the key is read and validated, what value applies when
it is missing or invalid, and what the value is used for.

## 1. Where the key is read and validated

- The key `waiting_notify_after_hours` is a member of the constant
  `POLICY_KEYS` in `tools/llmtool/llmtool/repair_policy.py`.
- `load_policy` in `tools/llmtool/llmtool/repair_policy.py` reads the policy
  file, which is located by the function `policy_path` in the same file, and
  then validates the content.
- In `load_policy` the validation of the key is: the file must be a JSON
  object (dict) whose keys are exactly a subset of the allowed set (the union
  of `schema_version`, `POLICY_KEYS`, the keys of `OPTIONAL_POLICY_LIMITS` and
  the `OPTIONAL_POSITIVE_POLICY_KEYS`); the key set must include `schema_version`
  and every key of `POLICY_KEYS`; and every key of `POLICY_KEYS`, including
  `waiting_notify_after_hours`, must be of type `int` with a value between 0
  and 1000 inclusive. `schema_version` must be the integer 1.
- The source policy file is `tools/llmtool/policy.json`; `policy_path` in
  `tools/llmtool/llmtool/repair_policy.py` finds it at the repository path and
  also accepts a packaged or installed copy in that order.

## 2. Which value applies when the key is missing or invalid

- In `tools/llmtool/policy.json` the current value of
  `waiting_notify_after_hours` is 24.
- In `load_policy` the key is mandatory: the whole key set of the file must
  contain every key of `POLICY_KEYS`, so a missing key, a value that is not of
  type `int`, or a value outside 0 to 1000 makes `load_policy` refuse with the
  `LLMToolError` code `POLICY_INVALID`. The same refusal happens for any read
  or parse failure, or for a file longer than the bound `_MAX_BYTES`
  (16 KiB).
- There is no default or fallback value for this key in the read scope:
  `waiting_notice_due` in `tools/llmtool/llmtool/repair_policy.py` reads
  `policy['waiting_notify_after_hours']` from the policy dict it was given and
  has no fallback for a missing key. A malformed reason or time in
  `waiting_notice_due` makes it notify at once; a malformed or missing key in
  the policy is instead refused by `load_policy` before any such dict is
  produced.
- For contrast, the function `repair_attempts_max` in
  `tools/llmtool/llmtool/repair_policy.py` fails closed (returns 0) when
  `load_policy` refuses, but it does not read
  `waiting_notify_after_hours`. No function in the read scope uses this key
  with a fallback.

## 3. What the value is used for

- The only place in the read scope that uses the key is the function
  `waiting_notice_due` in `tools/llmtool/llmtool/repair_policy.py`, with
  signature `waiting_notice_due(*, reason, waiting_since, now, policy)`.
- It returns `True` (the notice is due) when the reason is `'AUTH'`, because
  AUTH WAITING notifies at once.
- It returns `True` when the reason is not a member of the constant
  `WAITING_REASONS` in `tools/llmtool/llmtool/repair_policy.py` (the values
  `AUTH`, `QUOTA`, `ROUTE_UNAVAILABLE`, `NO_REVIEWER`, `DISPUTE`, `ENV`), or
  when `waiting_since` or `now` cannot be parsed with the format
  `%Y-%m-%dT%H:%M:%SZ`.
- Otherwise, for a valid non-AUTH reason and valid times, it returns `True`
  exactly when the difference `now - waiting_since` is at least
  `timedelta(hours=policy['waiting_notify_after_hours'])`. With the current
  value 24 from `tools/llmtool/policy.json`, the notice is due 24 hours after
  the Task entered WAITING.

## Basis check against row 21

- Row 21 of Issue #2281 states 24 hours and configurability of this delay.
  Both are consistent with the read scope: the current value 24 is present in
  `tools/llmtool/policy.json`, and the key is part of the policy file that
  `load_policy` in `tools/llmtool/llmtool/repair_policy.py` reads and
  validates.
- The comment above `OPTIONAL_POSITIVE_POLICY_KEYS` in
  `tools/llmtool/llmtool/repair_policy.py` documents other rows of the same
  limit table (rows 2, 15, 16, 17) but does not document row 21. The only
  in-scope record of the key's origin is its membership in `POLICY_KEYS` and
  its docstring in `waiting_notice_due` ("E5: AUTH notifies at once; any other
  WAITING after waiting_notify_after_hours."). Any further provenance of the
  row 21 value is outside the read scope.
