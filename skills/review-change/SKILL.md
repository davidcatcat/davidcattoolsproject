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
6. Check failure handling and observability.
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
- For provider/model transports, check that credentials, Authorization headers, raw HTTP error
  bodies, raw responses, prompts, and internal reasoning cannot leak through stdout, stderr,
  exceptions, or final replies. Include provider fields such as `reasoning_content` in that check.
- State validation evidence precisely: source review, unit test, mock integration, network
  reachability, authentication, real inference, backend session, repository access, and live
  provider test are separate results. Reachability is not inference; mock success is not live
  success; one backend session says nothing about another backend.
- Check that branch, merge, roadmap, and report wording matches the actual tested revision and
  validation state. Keep CURRENT, DONE, merged, and unmerged claims accurate.

## Completion criteria

- Entire intended diff was reviewed.
- Requirement and repo rules were checked.
- Material risks are called out concretely.
- Validation status is known.
