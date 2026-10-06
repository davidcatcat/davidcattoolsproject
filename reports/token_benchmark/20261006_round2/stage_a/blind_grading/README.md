# Round-2 Stage-A anonymous grading inputs

Semantic status: **BLIND_REVIEW_REQUIRED**. No grader has run.
Two independent graders and a separate tie-break on disagreement remain required.
A/B labels were privately randomized per fixture. The mapping is outside this
repository and is not provided to reviewers. All six final answers are unchanged.

**Blinding limitation:** some L02/L03 answers refer to a company-derived result.
Their own wording can reveal input provenance despite anonymous filenames. These
answers have not been edited or rerun to conceal this. They cannot be represented
as fully blinded. An independent review coordinator must address this limitation
before accepting any blind-review conclusion. These files are archival review
inputs, not a claim that every blinding gate passed.

| Anonymous outputs | Allowed frozen task inputs |
|---|---|
| L01-A.json, L01-B.json | [L01_large_test](../L01_large_test/) |
| L02-A.json, L02-B.json | [L02_large_log](../L02_large_log/) |
| L03-A.json, L03-B.json | [L03_large_context](../L03_large_context/) |

Graders may receive only an anonymous answer and that fixture's `fixture.txt`,
`gold.json`, `output_schema.json`, and `task.txt`. Do not give them token metrics,
company outputs, variant mapping, preregistration metadata, coverage/selection
results, PR descriptions or result reports. A coordinator must supply only allowed
files in an isolated review package; graders should not browse the repository/PR.

Record structured schema/gold misses separately from semantic fidelity: unknowns,
source/evidence support, unsupported causal claims, fact/inference/hypothesis
separation, and forbidden recommendations. Judge each answer against its task.
Do not repair answers or infer quality from schema validity. Compare anonymous
answers after individual grading. Preserve all deterministic misses. Disagreement
keeps quality parity unresolved and excludes the pair from quality-approved
aggregates until a separately documented independent tie-break. Stop after review.
