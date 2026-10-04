---
name: security-review
description: Perform a security baseline, threat model, or focused security review of an application, CLI, workflow, provider integration, or proposed execution boundary. Use when the user asks about security, threat modeling, OWASP/CWE/CVSS review, or before adding a materially larger attack surface such as command/file/Git execution.
---

# Security Review

Assess what an attacker or untrusted input can actually influence, what must be protected,
and which controls are required.

## Boundary

Security review is not ordinary code review and not formal product QA. It may consume
code-review/QA evidence, but its primary questions are trust, authority, data exposure
and exploitability.

For llmtool, treat model/provider output, retrieved content, project files, remote
frontends and user-supplied paths/commands as untrusted data unless a narrower
contract explicitly proves otherwise.

## Process

1. Pin the exact revision, scope and intended deployment/runtime model.
2. Identify assets: credentials, user/project data, diagnostics, source/Git state, filesystem/network authority, approvals and audit evidence.
3. Identify actors and trust boundaries: user, project config, provider/profile, local CLI runtime, company/external provider, filesystem, Git, CI, remote frontend and future executor.
4. Map data/action flows and authorization points. Capability, model output and successful parsing are never authorization.
5. Review attack classes relevant to the actual surface:
   - injection / command or argument construction;
   - path traversal, symlink/hardlink, TOCTOU and filesystem replacement;
   - approval/authentication/authorization bypass and replay;
   - secret/privacy leakage through stdout/stderr/logs/traces/debug/error bodies;
   - unsafe deserialization/parsing and malformed-input handling;
   - retry/fallback/trust-boundary data export;
   - dependency/supply-chain/build/CI assumptions;
   - race, rollback, recovery and partial mutation;
   - prompt/model-output injection into execution or policy decisions;
   - insecure defaults/misconfiguration and excessive authority.
6. Verify controls with source evidence and targeted probes/tests where practical. Distinguish design requirements from exploitable current findings.
7. Classify findings as `CONFIRMED SECURITY BUG`, `LIKELY SECURITY BUG`, `DESIGN GAP`, `THEORETICAL RISK`, or `ACCEPTED/EXPECTED`.
8. Map a finding to CWE when a specific weakness class fits. Use CVSS v4.0 for demonstrated vulnerabilities where severity scoring is meaningful; do not manufacture a numeric score for a design note.
9. Use OWASP ASVS and OWASP Top 10 as verification/reference frameworks when applicable. Do not force non-web CLI/system findings into unrelated OWASP categories.
10. Do not fix findings during an independent read-only security review unless the user explicitly authorizes a remediation phase. Confirmed defects should flow through `implement-feature` with regression/security tests, then return for re-review.

## Threat-model output

Record:

- protected assets;
- trust boundaries and data flows;
- attacker capabilities/assumptions;
- existing controls;
- confirmed vulnerabilities;
- design gaps that must be resolved before a future milestone;
- accepted/deferred risks with rationale;
- not-tested areas and environmental limits.

For a pre-execution baseline, explicitly derive requirements for command/file/Git
execution, approvals, sandboxing, secret minimization, audit durability, rollback,
network policy and provider data-export boundaries before implementation starts.

## Rules

- A warning is not a vulnerability without a plausible path and impact.
- Reachability, authentication, authorization and execution authority are separate.
- Redaction/compression does not by itself authorize data export.
- A cwd restriction is not a sandbox.
- Approval must bind to the exact action/state; model text saying "approved" is not approval.
- Do not weaken fail-closed behavior to improve availability without an explicit security decision.
- Do not disclose real secrets while testing a leakage path; use synthetic markers.

## Completion criteria

- Revision/scope/deployment assumptions are explicit.
- Assets, actors and trust boundaries are documented.
- Confirmed findings have evidence and realistic impact.
- CWE/CVSS/OWASP references are used only where they genuinely fit.
- Future security requirements are concrete enough to become implementation acceptance criteria.
- Unknowns and not-tested attack surfaces remain explicit.
