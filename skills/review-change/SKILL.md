---
name: review-change
description: Review a code change for correctness, regressions, security, maintainability, and test coverage. Use before committing important changes or when the user asks to review a diff, patch, branch, or implementation.
---

# Review Change

Review the actual diff against the requested behavior and repository rules.

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

## Completion criteria

- Entire intended diff was reviewed.
- Requirement and repo rules were checked.
- Material risks are called out concretely.
- Validation status is known.
