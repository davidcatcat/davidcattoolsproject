---
name: review-change
description: Review a code change for correctness, regressions, security-relevant boundaries, maintainability, test coverage, and validation provenance. Use before committing important changes or when the user asks to review a diff, patch, branch, or implementation. Use bug-hunt, security-review, or qa-validation for those specialist gates.
---

# Review Change

Review the actual diff against the requested behavior and repository rules.

## Boundary with specialist gates

This skill is the ordinary implementation/diff review. Do not stretch one review
label to cover every evidence type:

- use `bug-hunt` for an active search for reproducible defects in the current implementation;
- use `security-review` for a formal threat model/security baseline or exploit-focused assessment;
- use `qa-validation` for public/user-facing functional and acceptance testing.

A normal review may notice issues in any of those areas, but it does not automatically
satisfy a milestone gate that explicitly requires one of the specialist workflows.

## Process

1. Establish the review base and inspect the complete diff.
2. Read applicable repository guidance and the originating requirement/spec.
3. Check correctness and edge cases.
4. Check regression risk and backwards compatibility.
5. Check security and privilege boundaries where relevant.
6. Check failure handling and observability. For executable workflows, verify stable error classification, unique/correlatable run identity, useful normal-mode errors, structured trace/audit events, bounded retention, and a documented path from a user-visible failure to root-cause evidence.
7. Check tests for behavioral coverage rather than implementation coupling.
8. Look for accidental scope expansion, dead code, duplicated logic, and stale docs.
9. Run or verify the canonical checks when possible.
10. Report findings ordered by severity, with concrete file/line references when available.

If no material issue is found, say so directly and identify any testing or environment gaps that remain.

## Formal verdict format

A formal review (AGENTS.md *Formal Reviewer*) is posted as one new PR comment, never by
editing an earlier one. All agents share one GitHub login, so the login proves nothing:
only the Reviewer, working in its own read-only context with its own D6 reviewer record,
posts this format; a Developer or the PM never posts a `review_verdict` marker. The PM
checks the D6 reviewer record (distinct agent, session and native session from the
Developer) before merge. The comment starts with exactly these lines:

```text
<!-- activity: type=review_verdict actor=<Claude|Codex> target=PM status=<PASS|FIX|FAIL> -->
Verdict: <PASS|FAIL|BLOCKED> | Version: v<N> | HEAD: <40-hex reviewed commit>
LLMTOOL-REVIEW-BINDING v1 task=<task id> run=<run id> contract=<contract digest> head=<40-hex>
```

and, on PASS only, one more line right after them:

```text
FORMAL SECURITY/FUNCTIONAL/QA ACCEPT
```

- `actor` is the Reviewer's model family; it is the other family than the Developer.
- The binding line is required when the change runs through an llmtool Task; it names
  that Task's exact IDs and the reviewed HEAD.
- `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT` appears only on PASS. A FIX or FAIL comment omits
  it, and the latest verdict for a HEAD wins.
- Findings, gates and disposition follow these lines.

## Provider and runtime reviews

- Distinguish implementation capability from upstream API capability, authorization, permission,
  policy, and approval. Upstream support does not count as an implementation capability unless
  this implementation enables and safely supports it; upstream tool support alone cannot justify
  `shell=true`, `git=true`, or `code_edit=true`.
- Transport credentials and Authorization headers must never leak into any output or final reply.
- Check that diagnostics, traces, exceptions and debug/verbose modes do not leak credentials,
  Authorization headers, raw HTTP bodies, prompts or internal reasoning (including
  `reasoning_content`). Distinguish explicitly requested result/evidence content from accidental
  diagnostic echo; preserve the documented stdout/JSON contract and persistence defaults.
- For execution engines, verify policy/required approval precedes each side effect, including tests, repair and Git;
  changed actions/state invalidate old approvals. A cwd restriction alone is not a sandbox.
- State validation evidence precisely: source review, unit test, mock integration, network
  reachability, authentication, real inference, backend session, repository access, and live
  provider test are separate results. Reachability is not inference; mock success is not live
  success; one backend session says nothing about another backend.
- Check retry/fallback/repair behavior against explicit error policy. Auth/config/safety failures must not be retried blindly; model-reported success must not bypass deterministic validation; debug mode must not disable redaction, approval, or safety boundaries.
- Check that branch, merge, roadmap, and report wording matches the actual tested revision and
  validation state. Keep CURRENT, DONE, merged, and unmerged claims accurate.

## Cloud verification review

For llmtool Cloud/Mac changes, check
[the development workflow](../../tools/llmtool/WORKFLOW.md).
Verify the handoff names the tested implementation SHA, distinguishes Cloud/CI/mock
from Mac/live evidence, and leaves required pending checks merge-blocking. Check for
changes since validation before accepting old results. Documentation-only work needs
documentation checks, not an unnecessary live provider run.

## Completion criteria

- Entire intended diff was reviewed.
- Requirement and repo rules were checked.
- Material risks are called out concretely.
- Validation status is known.
