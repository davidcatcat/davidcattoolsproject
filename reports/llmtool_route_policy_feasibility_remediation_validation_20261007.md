# llmtool route-policy feasibility remediation validation (2026-10-07)

## Scope

This is a documentation/policy-only remediation of the findings in:

`reports/llmtool_route_policy_feasibility_review_20261007.md`

Base main:

`a6c3d792ce55ddbf9ea2956e2a3b3d31418bbe4f`

Reviewed remediation head:

`79973cbdb112b894b717c274fc9c6dde5f3d66d7`

No production/runtime code changed.
No Company or Codex model call was made.
No benchmark was rerun.
No A11b.2 implementation was started.

## F1 — savings claim scope

CLOSED.

Guidance now distinguishes:

- ordinary `test-triage` development use; and
- the specific L01 research pipeline that produced the positive savings observations.

The positive claim is explicitly limited to the measured pipeline:

- approximately 48 KiB canonical synthetic raw test output;
- complete deterministic FAILURE-ID coverage;
- deterministic ±2-line excerpt selection;
- selector cap `min(32 KiB, 20%)`;
- selected original raw text injected inline;
- no full-raw re-read by Codex;
- temperature 0;
- `enable_thinking=false`;
- `max_tokens=4096`;
- timeout 120.

The two positive observations remain 39.82% and 37.55% and are not generalized.

Ordinary/plain `llmtool ask --skill test-triage` is now explicitly unvalidated for
token-saving claims.

Round-1 B01 is explicitly retained as the counterexample:
small bounded evidence measured −103.32%, and the first default-output-budget attempt
hit `MODEL_OUTPUT_INCOMPLETE`.

## F1 future feasibility gap

The deterministic coverage/selector mechanics are recorded as frozen benchmark
mechanics, not normal runtime functionality.

A future productionized savings route requires a separate deterministic selector for
real test formats such as pytest/unittest plus a new Company-only Phase 0.

Until then A11c must not advertise ordinary `test-triage` as a production
token-saving route.

## F2 — stale guidance

CLOSED.

- stale `cheap-first integration` wording in WORKFLOW was replaced with
  route-by-task integration;
- CLAUDE.md now points sessions to route/pipeline-specific evidence;
- AGENTS.md now points Codex sessions to the company delegation skill before making
  savings claims.

## F3 — P1 accounting guidance

CLOSED.

The company delegation skill now records valid usage from successful or safely parseable
failed provider responses when P1 exposes a validated triplet.

The Round-4 prerequisite now explicitly requires future benchmark wrappers to consume
`LLMToolError.provider_metadata` or the equivalent safe diagnostics event; otherwise
P1 would not improve failed-call accounting.

## Consistency checks

Confirmed across root README, llmtool README, ROADMAP, WORKFLOW, company delegation
skill, CLAUDE.md and AGENTS.md:

- no plain test-triage route is described as generally validated for savings;
- no stale `cheap-first integration` or `company-first integration` wording remains;
- L02/log-triage remains EXPERIMENTAL;
- L03/context-pack remains EXPERIMENTAL;
- Company-only Phase 0 still precedes new paired Codex benchmark work;
- Round 4 remains optional/deferred and does not block A11b.2;
- A11b.2 remains NOT STARTED.

## Final status

```text
ROUTE-POLICY FEASIBILITY REMEDIATION:
PASS

F1 benchmark-pipeline scoping:
CLOSED

F1 real-format selector gap:
TRACKED AS FUTURE WORK

F2 stale guidance:
CLOSED

F3 P1 accounting guidance:
CLOSED

Production/runtime changes:
NONE

Company calls:
0

Codex benchmark calls:
0

A11b.2:
NOT STARTED

Ready for documentation-only PR review:
YES
```
