# Lineage end-to-end test plan (#2367 P1-3)

Status: design and acceptance note only.

This note specifies a later test for one lineage path:

review FAIL, developer repair, review FAIL, AI Debug, developer repair, review PASS, merge,
with a Claude-to-AGY developer reroute on the second repair that continues the same lineage
(#2200).

No code and no test is changed here. The only change is this note. The test PR that
implements this plan waits for **#2367 P0-2** and for a **P1 code authorization**. Until
both exist, this note is the deliverable.

Test data is synthetic and labelled. This note contains no credentials, and the later test
must not contain credential-shaped strings; see "Test data rule" below.

## 1. Path and observable assertions

The notation `H1`, `H2` and `H3` means the reviewed head after each developer step. `N`
means the pull request number. `T` means the root Task ID.

| Step | Action | Observable assertion |
| --- | --- | --- |
| 1 | Formal review FAIL on `H1`. | A REVIEWER completion event is PENDING for `H1`; the verdict is `REVIEW_FAIL`; `review_repair_claims` is empty. |
| 2 | Review repair 1 is claimed and dispatched to the Claude developer. | Exactly one claim exists, family `pr-N`, version 1, attempt 1, state `DISPATCHED`; a repair task ID is recorded; the packet failed head is `H1`; no `high_debug` entry exists; the packet has no Debug report digest. |
| 3 | Developer repair 1 publishes `H2`. | `H2 != H1`; the claim is still one and still in family `pr-N`; the developer record is still the Claude route; the repair task still continues `T`'s lineage (exact continuation field is UNKNOWN, see below). |
| 4 | Formal review FAIL on `H2`. | A second REVIEWER event/claim exists for `H2`; calling the repair gate refuses with `REPAIR_DEBUG_REQUIRED`; no second claim is written; no repair task is dispatched. |
| 5 | AI Debug examines `H2` and records `RCA_CONFIRMED`. | One `high_debug` entry exists for family `pr-N` and examined SHA `H2`, with `RCA_CONFIRMED`, a report digest, a rules digest, a D6 name, and a HIGH_DEBUG completion event; the diagnosis workspace is clean. |
| 6 | Review repair 2 is claimed with the Debug report bound, and the developer reroutes from Claude to AGY. | Exactly two claims exist in family `pr-N`, attempt 2, state `DISPATCHED`; the second claim and packet carry the Debug report digest; family, task ID, lineage and repair task ID are unchanged by the reroute; the developer record names the AGY route; the first claim record is preserved. |
| 7 | AGY repair publishes `H3`. | `H3` differs from `H2`; still two claims in one family; the AGY developer record is the one bound to the repair; lineage is unchanged. |
| 8 | Formal review PASS on `H3`. | The verdict is PASS for `H3`; no new repair claim is written; the two existing claims stay in state `DISPATCHED` and no claim becomes `FAILED` or `UNKNOWN`; the review result names `H3`. |
| 9 | Merge `H3`. | The merge record names `H3`; the Task is completed; the two `DISPATCHED` claims are unchanged and no claim is added; the lineage is not restarted or duplicated. |

Claim lifecycle: `tools/llmtool/llmtool/review_repair.py::_LIVE_CLAIM_STATES` lists
`CLAIMED`, `DISPATCHED` and `UNKNOWN` as live. A successful repair moves its claim from
`CLAIMED` to `DISPATCHED` through `tools/llmtool/llmtool/review_repair.py::_update_claim_state`
and leaves it there. `tools/llmtool/llmtool/review_repair.py::repair_review` writes
`FAILED` only when a retry replaces a dispatched claim, and `UNKNOWN` only when the adapter
raises. The PASS and merge steps must therefore not assert that no repair claim is open.
Step 8 asserts no new claim and no state change; step 9 asserts the same persisted
`DISPATCHED` claims. Whether an advance-side step settles or waives a live `DISPATCHED`
claim before merge is UNKNOWN (task advance is outside the read scope); the later test
must assert the observed advance behaviour rather than require a close.

Two admission details must be asserted as part of the path, not bypassed:

- `tools/llmtool/llmtool/review_repair.py::_review_gate` refuses `REPAIR_DEBUG_REQUIRED`
  when the second FAIL has no `RCA_CONFIRMED` Debug on its failed head. Step 4 proves the
  refusal happens before any claim or dispatch.
- `tools/llmtool/llmtool/high_debug.py::run_high_debug` admits `failed_corrections` only
  when it is at least 2, and it then requires at least that many claims in the family.
  At step 5 only one repair claim exists, so the Debug leg must use the
  `early_reason` form (`failed_corrections=None`) or the P1 code must change that
  admission. The later test must follow the authorized #2367 P0-2/P1 code and must not
  pass a value that the production check rejects. A useful negative assertion is that
  `failed_corrections=1`, and `failed_corrections=2` with one claim, both refuse with
  `HIGH_DEBUG_ARGUMENT_INVALID` before any D6 or provider call.

## 2. Seams and fixtures for each step

The read scope for this note was:

- `tools/llmtool/llmtool/high_debug.py`
- `tools/llmtool/llmtool/review_repair.py`

Every in-scope reference below comes from those two files. Files outside the read scope
are named only where the in-scope code calls them, and their contents are UNKNOWN.
Advance-side modules (task advance, drive, reroute, merge) were not read and are marked
UNKNOWN.

### Step 1 and step 4: review FAIL input

Existing module and functions:

- `tools/llmtool/llmtool/review_repair.py::repair_review` reads the FAIL.
- `tools/llmtool/llmtool/review_repair.py::_failed_event` selects the newest PENDING
  REVIEWER event and its review verdict comment.
- `tools/llmtool/llmtool/review_repair.py::_remote_test_failure` and
  `tools/llmtool/llmtool/review_repair.py::_ci_failure` are alternative packet builders;
  this path uses the verdict path, not the test-failure path.

Injectable seams:

- `repair_review(github_get=...)` replaces the GitHub comment read.
- The task record itself is the fixture: `review_dispatch_claim` (a dict or list) and
  `completion_events` with `source='REVIEWER'`, `state='PENDING'`, the matching
  `source_sha`, and `session_id='review-' + event_id`.
- The verdict body is synthetic text with a `Finding: F1 [blocking]` line so
  `tools/llmtool/llmtool/review_repair.py::_review_gate` sees a blocking finding.

Missing seams:

- `tools/llmtool/llmtool/review_result.py::_comment_verdict` is called inside
  `_failed_event`; its exact fixture surface is UNKNOWN because that file is outside the
  read scope. The fake `github_get` page must satisfy the real parser.
- The production review-failure writer is outside the read scope (advance-side); the test
  fixture therefore constructs the record shape directly. The function that writes it is
  UNKNOWN.

### Step 2 and step 6: repair claim, gate and dispatch

Existing module and functions:

- `tools/llmtool/llmtool/review_repair.py::repair_review`
- `tools/llmtool/llmtool/review_repair.py::_review_gate`
- `tools/llmtool/llmtool/review_repair.py::_record_repair_task`
- `tools/llmtool/llmtool/review_repair.py::_delegate_repair`
- `tools/llmtool/llmtool/review_repair.py::_current_d6_head`
- `tools/llmtool/llmtool/review_repair.py::_worker_key`

Injectable seams:

- `repair_review(store=..., task_id=..., executor_task=..., pr_number=..., family=...)`
  accepts the task store and the exact head.
- `repair_review(current_head=...)` avoids reading a live D6 registration for the head
  check.
- `repair_review(repair_task_recorder=...)` replaces the repair Task recorder.
- `repair_review(dispatch_adapter=...)` replaces `_delegate_repair` entirely.
- `repair_review(delegate_callable=...)` replaces only the delegate call inside
  `_delegate_repair`.
- `repair_review(retry_claim_id=...)` exercises a replay of a dispatched claim.
- The claim itself is observable through the store: `review_repair_claims`, `state`,
  `attempt`, `version`, `family`, `repair_task_id`, `packet_sha256` and, on the second
  repair, `high_debug_report_sha256`.

Missing seams:

- `tools/llmtool/llmtool/review_repair.py::_policy` calls a loader named
  `tools/llmtool/llmtool/repair_policy.py::load_policy`; `repair_review` has no policy
  parameter. That file and its exact function signature are outside the read scope and
  are UNKNOWN. The test should use the real policy file when possible, or patch the
  policy loader explicitly and restore it.
- `tools/llmtool/llmtool/review_repair.py::_review_gate` receives counts from a call
  named `tools/llmtool/llmtool/lineage_counts.py::lineage_counts`; there is no counts
  parameter at `repair_review`. That file is outside the read scope and its fixture
  surface is UNKNOWN. Prefer a real temporary task store so the chain counts are produced
  by production code.
- `tools/llmtool/llmtool/worker_dispatch.py::evaluate` and
  `tools/llmtool/llmtool/worker_availability.py::fresh_quota_fail` are called by
  `repair_review` with no injection point. Their files and signatures are outside the
  read scope and UNKNOWN. Use the real dispatch configuration or patch them explicitly.
- `repair_review` checks `shutil.which('claude')` for the Claude CLI route. There is no
  executable-path parameter; the test must provide the path or patch the lookup. This is
  a test-environment seam, not a production check to weaken.
- `tools/llmtool/llmtool/review_repair.py::_registered_d6` calls
  `tools/llmtool/llmtool/task_completion.py::read_d6_registration` and
  `tools/llmtool/llmtool/task_completion.py::collect_git_snapshot`. There is no injection
  point, but `dispatch_adapter` bypasses `_registered_d6` when the test is not exercising
  a live D6. Its exact fixture surface is UNKNOWN.

### Step 3 and step 7: developer publishes the repaired head

Existing module and functions:

- `tools/llmtool/llmtool/review_repair.py::repair_review` reaches the provider only
  through `dispatch_adapter`, and records the claim as `DISPATCHED` after the adapter
  returns.
- `tools/llmtool/llmtool/review_repair.py::_delegate_repair` is the production delegate
  path when `dispatch_adapter` is not supplied.

Injectable seams:

- `dispatch_adapter` returns the result and can record the simulated Claude or AGY
  publication of `H2` or `H3`.
- `current_head` makes the next repair's head check deterministic.

Missing seam:

- The real developer launch, D6 head publication and push are advance-side. Their file
  and function are UNKNOWN. The later test should assert the observable head and claim
  invariants through the fake adapter, and add a live-route case only after the P1 code
  names the real publication seam.

### Step 5: AI Debug

Existing module and functions:

- `tools/llmtool/llmtool/high_debug.py::run_high_debug`
- `tools/llmtool/llmtool/high_debug.py::run_high_debug_with_fallback`
- `tools/llmtool/llmtool/high_debug.py::_admit_debug`
- `tools/llmtool/llmtool/high_debug.py::_reserve_debug`
- `tools/llmtool/llmtool/high_debug.py::_debug_rules`
- `tools/llmtool/llmtool/high_debug.py::_create_d6`
- `tools/llmtool/llmtool/high_debug.py::_run_codex`
- `tools/llmtool/llmtool/high_debug.py::_run_claude`
- `tools/llmtool/llmtool/high_debug.py::_session_observed`
- `tools/llmtool/llmtool/high_debug.py::_outcome_status`
- `tools/llmtool/llmtool/high_debug.py::_post_record`

Injectable seams:

- `run_high_debug(runner=...)` replaces the provider launch. The fake runner receives
  `prompt`, `cwd` and `output_path`, and returns an object with `returncode`, `stdout`
  and `stderr`. It writes the report to `output_path`.
- `run_high_debug(create_d6=...)` replaces the D6 creation and returns a diagnosis root.
- `run_high_debug(read_registration=...)` replaces the D6 registration read.
- `run_high_debug(sessions_dir=...)` points `_session_observed` at a synthetic rollout
  tree.
- `run_high_debug(route='codex')` is the normal path; `route='claude'` is admitted only
  through `run_high_debug_with_fallback` and its one-use grant.
- The store is the task store; the recorded entry is observable through `high_debug`, and
  the completion event through `completion_events`.

Missing seams:

- `_debug_rules` has no injection point for the main SHA or the card source. It calls
  `tools/llmtool/llmtool/task_completion.py::read_authenticated_main_sha`,
  `tools/llmtool/llmtool/task_completion.py::ensure_main_commit`,
  `tools/llmtool/llmtool/skill_manifest.py::build_debug_cards` and
  `tools/llmtool/llmtool/skill_manifest.py::role_card_prefix`. Those files are outside
  the read scope; their exact patch surfaces are UNKNOWN. The test must supply a local
  authenticated main fixture or patch those calls, and must not bypass `_debug_rules`.
- `_d6_taken` runs a Git probe with no injection point. The default `_create_d6` is a
  subprocess. The test should use `create_d6` and a temporary repository/D6 fixture; the
  lifecycle helper's fixture surface is outside the read scope and UNKNOWN.
- `run_high_debug` calls
  `tools/llmtool/llmtool/task_completion.py::collect_git_snapshot` on the registration
  root and compares its head with the expected head. There is no injection point, so the
  fake registration root must be a temporary Git repository whose HEAD is `H2`; the
  helper's fixture surface is UNKNOWN.
- `_post_record` posts to GitHub through `_post_github_record`. There is no post
  parameter. The test should fake the GitHub boundary so no network is used; the screen
  and record checks stay real. The exact trusted-repository helpers are outside the read
  scope and UNKNOWN.
- `_screen_hits` and `_emitted_sensitive` are production checks. A normal successful
  report must pass them; a refusal-flow case must inject the screen function and still
  assert the real refusal, per the test data rule.

For step 5 the fake report must contain all `REPORT_SECTIONS` and a single status word
`RCA_CONFIRMED` on its own line in the Outcome/cost section. `_outcome_status` turns a
missing section or a duplicate status into `REPORT_INVALID`, so the assertion that the
entry is `RCA_CONFIRMED` also proves the report shape.

The Codex-to-Claude AI Debug fallback (quota or login) is a separate branch. It can be
covered by a small extra case using `run_high_debug_with_fallback` and a stateful runner
that fails the first call with `HIGH_DEBUG_QUOTA_EXHAUSTED` and succeeds on the second.
It is not the #2200 Claude-to-AGY developer reroute and should not be used as a substitute
for it.

### Step 6: Claude-to-AGY reroute

Existing module and functions:

- `tools/llmtool/llmtool/review_repair.py::_worker_key` maps `agy` to the `agy` worker.
- `tools/llmtool/llmtool/review_repair.py::_delegate_repair` passes the developer's
  `provider_profile` and uses `mode='direct'` for `agy`.
- `tools/llmtool/llmtool/review_repair.py::_record_repair_task` permits `agy` as a repair
  route.
- `tools/llmtool/llmtool/review_repair.py::repair_review` reads the developer record and
  preserves the claim family and repair task ID.

Injectable seams:

- `dispatch_adapter` is the clean seam for a simulated reroute: the test can return an
  AGY result and assert the claim and lineage invariants without launching a provider.
- `repair_task_recorder` can record the repair task with the AGY developer shape.
- `current_head` keeps the head check deterministic.

Missing seam:

- The reroute decision is not in `review_repair.py`. The advance-side function that
  changes the developer route from Claude to AGY and continues the lineage is UNKNOWN,
  as is its file and function name. The later test should assert the contract it can
  observe now (same Task, same lineage, same family, same repair task ID, same claim,
  new `provider_profile='agy'`) and add a direct test of the real reroute function once
  the P1 code names it. Do not fabricate a reroute function in the test.

### Step 8: review PASS

Existing module and functions:

- `tools/llmtool/llmtool/review_repair.py::repair_review` has `auto_review` and
  `review_dispatcher` seams.
- `tools/llmtool/llmtool/review_repair.py::_current_d6_head` decides whether the repair
  is published before a follow-up review.
- `tools/llmtool/llmtool/review_repair.py::_update_claim_state` and
  `tools/llmtool/llmtool/review_repair.py::_LIVE_CLAIM_STATES` define the persisted claim
  state: a successful repair stays `DISPATCHED` and counts as live.

Injectable seams:

- `review_dispatcher` replaces
  `tools/llmtool/llmtool/review_dispatch.py::dispatch_review`.
- The PASS verdict is part of the same synthetic verdict-comment fixture used by
  `_failed_event`, with a PASS outcome instead of FAIL.

Missing seams:

- `tools/llmtool/llmtool/review_result.py` and
  `tools/llmtool/llmtool/review_dispatch.py` are outside the read scope; their exact
  fixtures and function signatures are UNKNOWN. Use the real parser with a synthetic
  comment where possible.

### Step 9: merge

This step is entirely advance-side. The file, function and task-record fields for the
merge are UNKNOWN. The later test should assert only the stable observable contract:
the merged SHA is `H3`, the Task is completed, and the two repair claims are still the same
`DISPATCHED` records with no added claim. It must not assert that the claims are closed,
because the repair path leaves a successful claim in `DISPATCHED`. How merge treats a live
`DISPATCHED` claim (settle, waive or ignore it) is advance-side and UNKNOWN; assert what the
authorized code does and do not invent a merge or a claim-closing step.

## 3. Fixture set for the later test

The test should be built from these fixture concepts. Existing fixture names in the
repository are UNKNOWN because the test tree was outside the read scope; reuse them if
they exist, otherwise add them in the new test module.

| Fixture | Purpose |
| --- | --- |
| Temporary task store | Holds the root Task, repair claims, completion events and `high_debug` entries; gives the real `lineage_counts` code real records. |
| Synthetic verdict pages | Returned by the fake `github_get`; FAIL and PASS bodies with labelled synthetic text. |
| Fake repair recorder | Records a repair task and preserves the Task and lineage binding. |
| Fake dispatch adapter | Records the provider route and returns a result; simulates Claude then AGY on the reroute leg. |
| Fake Debug runner | Writes a full report to `output_path`; can also fail with a quota code for the fallback branch. |
| Fake D6 creator and registration | Returns a clean temporary Git worktree as the diagnosis root plus a registration with the expected `result_sha`, `root` and `common_dir`; the registration root is also a temporary Git repository whose HEAD is the expected head. |
| Synthetic sessions tree | Gives `_session_observed` one model and one effort so the entry has a known observed model. |
| Fake GitHub poster | Replaces `_post_github_record` so `_post_record` uses no network; the screen and record logic stay real. |

All fixture text is labelled synthetic. No fixture contains a credential-shaped string.
Refusal-flow cases inject the screen function and still assert that the production refusal
happens; they do not disable, bypass or weaken the screen.

## 4. Expected files and module size for the later test PR

Proposed file list, assuming the repository keeps llmtool tests under
`tools/llmtool/tests/` (confirm against the test tree before the PR):

- `tools/llmtool/tests/test_lineage_e2e_2367.py`
  - Main end-to-end module.
  - Estimated 550 to 750 lines.
  - About 8 to 12 test functions covering the steps above, plus the two negative
    admission assertions and the optional Debug fallback branch.
- `tools/llmtool/tests/lineage_e2e_fixtures_2367.py`
  - Synthetic fixture builders for task records, verdict pages, claims and Debug
    reports.
  - Estimated 150 to 220 lines.

The later PR is expected to be a test-only change: no production file and no existing
test file should be modified. If a missing seam genuinely requires a production change,
that change belongs in the P1 code PR, not in the test PR.

The test PR waits for **#2367 P0-2** and for a **P1 code authorization**. It must not be
opened before both, and it must not assume the UNKNOWN advance-side seams above.

## 5. Test data rule (Owner #763 A)

- Ordinary tests use labelled synthetic text only; no credential-shaped strings.
- A refusal-flow test mocks, monkeypatches or injects the screen function and still checks
  the real refusal behaviour.
- Only a test of the sensitive detector itself may use the synthetic-data exception, with
  independent review and no real credential.
- No production check is disabled, bypassed or weakened for test convenience.

If any of this cannot be met, the test is BLOCKED rather than weakened.

## 6. Check (docs) constraints

Check (docs) was not run in this bounded Task; the note is written to the observed
constraints so the PM can run it.

- The only change is this note; no code or test file is touched.
- Code references name a repository-relative file and a function, in the form
  `<repo-relative path>::function_name`.
- No line anchors are used.
- No credentials, personal data or restricted logs are included.

## 7. Open items

- The advance-side reroute function and its file/function name are UNKNOWN.
- The advance-side merge function and its task-record fields are UNKNOWN.
- The advance-side treatment of live `DISPATCHED` repair claims at PASS and merge is
  UNKNOWN; the note asserts only that the claims stay unchanged unless the authorized
  code visibly settles them.
- The exact existing task-store and test fixtures are UNKNOWN.
- The exact patch surfaces of `tools/llmtool/llmtool/repair_policy.py`,
  `tools/llmtool/llmtool/lineage_counts.py`,
  `tools/llmtool/llmtool/worker_dispatch.py` and
  `tools/llmtool/llmtool/worker_availability.py` are UNKNOWN.
- The authorized #2367 P0-2/P1 form of the second-FAIL Debug admission (the
  `early_reason` form versus a changed `failed_corrections` rule) must be read from the
  P1 code when it exists.
