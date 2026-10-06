# Stage-B L01 blind quality package

**BLIND_REVIEW_REQUIRED / PENDING.** No Stage-B grader has run.
Two independent graders are required for this new confirmation pair; Stage-A
quality verdicts do not grade these answers. Resolve disagreement through a
separately documented independent tie-break before declaring quality parity.

Allowed inputs only:

- `L01-C.json` and `L01-D.json`: unchanged final answers; C/D assignment was freshly
  randomized privately and is not published.
- Frozen [L01 fixture](../../stage_a/L01_large_test/fixture.txt),
  [gold](../../stage_a/L01_large_test/gold.json),
  [schema](../../stage_a/L01_large_test/output_schema.json), and
  [task](../../stage_a/L01_large_test/task.txt).

Do not access variant mapping, token metrics, company outputs, results/reports,
PR descriptions, Stage-A answers/results or preregistration metadata. A coordinator
must supply an isolated allowed-file package rather than grant repository browsing.

Grade each anonymous answer against the frozen task/gold/schema, preserving
structured misses and checking evidence fidelity, required unknowns and forbidden
unsupported root-cause/repair claims. Schema validity alone is not semantic PASS.
Do not repair outputs or guess their provenance. After individual grades compare
quality parity. Record each grader's allowed-input compliance; disagreement leaves
quality unresolved. Do not combine this pair with any previous result, assess
savings, or run a model/provider. Stop after review.
