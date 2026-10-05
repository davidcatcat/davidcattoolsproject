# llmtool company-LLM dogfood skills validation handoff — 2026-10-06

## Scope

Repository: `davidcatcat/davidcattoolsproject`

Base main: `a8bbd6de837437fb8bd60a43460b6b8af62a88b8`

Feature branch: `feat/llmtool-company-dogfood-skills-20261006`

Implementation head before this report: `de9a5d460c824be76a8a40d74ae33f0e10d7c6c8`

This is a development-time dogfood support slice. It does **not** start A11b.2 or A11c,
does not add runtime routing, an executor, retry/fallback, mutation, approval authority,
a new provider, or a new output parser.

## Delivered

### Repository Agent Skill

`skills/company-llm-delegation/SKILL.md`

Purpose:

- deterministic/no-model work first;
- bounded permitted semantic triage through llmtool to an explicitly selected company profile;
- strong-model/source/raw-evidence authority for root cause, code changes, security, test correctness and merge;
- explicit data-permission basis and redaction/bounding before delegation;
- no silent external-provider fallback;
- bounded delegation metrics without copying sensitive raw logs into reports.

### llmtool runtime skills

- `test-triage` v1 — evidence-only failure grouping/classification; no root-cause/fix/PASS authority.
- `log-triage` v1 — evidence signatures/timeline/pattern compression; no root-cause/command/PASS authority.
- General evidence normalization reuses existing `context-pack`; no duplicate `evidence-summary` skill was added.

The two new triage resources are provider-agnostic declarative prompts. They are loaded
by the existing A3 skill framework and do not know or select the company provider.

`test-triage` and `log-triage` request exact JSON in their prompt but this first slice
adds no parser for them. Schema adherence is therefore a measured dogfood property, not
an enforced runtime guarantee. For general evidence normalization, the delegation skill
uses the existing parsed `llmtool context-pack --json` path.

## Static/offline validation

The skill discovery test has been updated so both JSON and human `llmtool skills`
listings include:

`analyze-log, context-pack, log-triage, review-diff, test-triage`.

A permanent test also verifies the two dogfood triage resources retain the key
boundaries: supplied material only, no root-cause-as-fact, no PASS/SAFE/DONE/VERIFIED/
merge-ready declarations, and caller comparison against original evidence.

The branch CI is the authoritative offline test evidence. Do not treat an older
intermediate run as final-head evidence.

## Claude review targets

Independently verify:

1. `company-llm-delegation` is a development workflow, not A11c routing.
2. Deterministic work is not delegated to any model.
3. Company-model input requires explicit data permission, bounding and redaction.
4. `test-triage` / `log-triage` cannot claim root cause, propose fixes, decide test correctness, approve security/QA, or approve merge.
5. No model confidence score is used for authority/routing.
6. `context-pack` is reused instead of duplicating evidence-summary.
7. Existing A3 loader/provider boundaries are unchanged; provider != skill.
8. The new resources are packaged by the existing `skills/*/SKILL.md` package-data rule without a pyproject change.
9. The test listing expectations and new prompt-boundary test are correct.
10. The F2/S1 follow-up from PR #33 is closed: CLAUDE.md is synchronized, delegation records include data permission basis, and reports have a raw-sensitive-log redaction rule.

## Mac + VPN live dogfood — REQUIRED BEFORE MERGE

Run from the canonical Mac repository only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Prerequisites:

- checkout/fetch this exact feature revision safely without discarding unrelated work;
- company VPN connected;
- existing configured company profile selected explicitly as `PROFILE_NAME`;
- llmtool executable/venv verified to come from this checkout.

Use only synthetic, non-sensitive input.

### 1. Runtime skill discovery

```sh
tools/llmtool/.venv/bin/llmtool skills --json
tools/llmtool/.venv/bin/llmtool skill show test-triage
tools/llmtool/.venv/bin/llmtool skill show log-triage
```

Expected: both new skills are present, version 1, and listing/show remains offline.

### 2. Company test-triage

Create a small synthetic failure file containing only a command/check name, exit 1,
two repeated timeout failures and one assertion mismatch. Then run:

```sh
tools/llmtool/.venv/bin/llmtool ask \
  --provider PROFILE_NAME \
  --skill test-triage \
  --file SYNTHETIC_TEST_FAILURE_FILE \
  --verbose
```

Capture stdout and stderr separately.

Required evidence:

- exit 0;
- stdout is one JSON object and parses successfully;
- required fields are present, no extra fields;
- repeated failures are grouped only when supported;
- no invented files/tests/causes;
- no root-cause-as-fact, code-fix, PASS/SAFE/DONE/merge claim;
- stderr contains only safe verbose metadata; record reported token usage/latency or unknown.

### 3. Company log-triage

Use a small synthetic log with repeated timeout messages, one later recovery line and
an explicitly missing root-cause detail. Run:

```sh
tools/llmtool/.venv/bin/llmtool ask \
  --provider PROFILE_NAME \
  --skill log-triage \
  --file SYNTHETIC_LOG_FILE \
  --verbose
```

Required evidence:

- exit 0;
- stdout parses as the requested JSON object;
- signatures/patterns/timeline stay within supplied evidence;
- missing causal information remains unknown;
- no NEXT_COMMANDS, root-cause-as-fact, code-fix, PASS/SAFE/DONE/merge claim;
- no credential/private-config reproduction.

### 4. Parsed context-pack reuse

Use a small synthetic evidence file and run:

```sh
tools/llmtool/.venv/bin/llmtool context-pack \
  --provider PROFILE_NAME \
  --file SYNTHETIC_EVIDENCE_FILE \
  --json \
  --verbose
```

Required evidence: exit 0 and the existing A4 parser accepts the response. Facts,
inferences, hypotheses and unknowns must remain separated.

### 5. Record one delegation entry

Record only:

- task type / runtime skill;
- explicit company profile;
- data permission basis: synthetic non-sensitive test evidence;
- input bytes;
- reported tokens or unknown;
- latency;
- schema validity;
- Codex adoption/override;
- deterministic/independent verification result.

Do not copy the complete raw input/output into the permanent report merely to prove the
call occurred.

## Merge status

- Offline implementation: COMPLETE pending final exact-head CI.
- Independent Claude review: REQUIRED / NOT YET RECORDED.
- Mac + VPN live dogfood: REQUIRED / NOT-TESTED.
- A11b.2: NOT STARTED.
- A11c production routing: NOT STARTED.
- Merge: BLOCKED until required review, final-head CI and Mac live dogfood pass.
