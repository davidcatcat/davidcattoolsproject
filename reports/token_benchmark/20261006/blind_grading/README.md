# Independent blind grading package

Status: **BLIND_REVIEW_REQUIRED**. These are six final structured analysis outputs,
not model reasoning transcripts. There is no quality PASS verdict in this package.

Give the independent grader **only this directory** and the three adjacent fixture
directories (`fixture.txt`, frozen `gold.json`, `output_schema.json`, `task.txt`).
Do not give the grader `results.json`, the pilot report, company outputs, raw Codex
JSONL, token metrics, or the private assignment. Reading those before grading may
compromise blinding and must be disclosed. A/B assignment is random per fixture;
the mapping is retained only in the local temporary measurement directory.

For B01, B02 and B03, assess A and B against the same frozen gold, independently
of apparent writing style. Do not infer which method produced an output. Record:

- Identifier/group/event/fact coverage and supporting source evidence.
- Unknowns and causal uncertainty preserved.
- Unsupported root-cause claims, commands, or promotion of hypotheses into facts.
- ContextPacket category/source/locator correctness for B03.
- Every semantic review item specified in the corresponding gold.
- Semantic equivalence of any failed literal predicate, without editing gold or
  silently changing the deterministic result.
- Final quality of each output and pair parity, with bounded source references.

The deterministic literal/group checks are reproducible using `../validation.py`.
They cannot prove causal correctness or semantic equivalence. An executing measured
Codex session cannot grade its own quality. The coordinator has not supplied a
semantic PASS, and no second measured repetition is authorized by this package.
