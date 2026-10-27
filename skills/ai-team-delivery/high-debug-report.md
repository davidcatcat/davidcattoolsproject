# High Debug report — Codex CLI `gpt-6.1-sol`

**Normative Owner decision: 2026-10-27** ([Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6437493012)). This report is mandatory for a PM-assigned High Debug Task. Consult [PM Continuation](pm-continuation.md), [Defect Triage](defect-triage.md) and [AGENTS.md](../../AGENTS.md). Do not claim a live `gpt-6.1-sol` invocation unless model/version and runtime evidence are verified.

## Trigger, identity and authority

Default trigger: **two failed corrections of the same confirmed root-cause/finding family**; high-risk or deep technical uncertainty may trigger earlier with PM evidence. The preferred diagnostic Worker is **`codex-developer` / Codex CLI / `gpt-6.1-sol`**, high reasoning when supported. Use only with allowed Worker dispatch, actual model availability, permitted evidence/egress, bounded task budget and verified D6 read-only context. The current per-assignment single-REPAIR gate is unchanged. Do not purchase or silently substitute a provider/model; a Debug report never authorizes mutation or overrides independent review.

## Mandatory GitHub report

| Field | Required content |
| --- | --- |
| **Identity and source** | Original Task/PR and High Debug Task ID; exact 40-character SHA, actual Worker/provider/model and reasoning, failed correction count/family, permitted source scope and report date. |
| **Symptom and reproduction** | Error identifiers; exact reproducer/test and expected vs actual behavior; confirmed evidence references; what the two attempted fixes changed and why they failed. No secrets or private logs. |
| **Root Cause — REQUIRED** | Precise broken invariant, data/control flow, function call or boundary condition that causes the failure. Cite the source and reproducer at the analyzed SHA. Clearly mark **CONFIRMED**, **HYPOTHESIS** or **UNKNOWN** and explain why; do not present an inference as proven fact. |
| **Exactly what to modify — REQUIRED** | **Repository-relative paths**, functions/classes/config keys, exact line ranges or unambiguous sections tied to SHA, **what code/logic/configuration needs to change**, why, and the expected before/after behavior. Include related callers/tests. If unconfirmed, provide concrete candidate locations **labelled conditional** and the smallest probe, not a fabricated patch. |
| **Verification** | Regression test files/test names; command(s) or safe probe, expected result, affected platforms, required formal Reviewer + exact-head Mac/Linux CI and runtime acceptance. |
| **Risks and handoff** | Security/compatibility/performance risks, rollback/containment, bounded implementation scope, responsible authorized Developer and required D6 single-writer transition. |
| **Outcome/cost** | `RCA_CONFIRMED`, `RCA_UNCONFIRMED` or `BLOCKED`; provider/token/cost information or `UNKNOWN`, remaining uncertainties and PM next action. |

### Required report example (format only, not actual evidence)

```text
HIGH DEBUG / Task: <id> / PR: <url> / examined SHA: <40-hex>
Worker: codex-developer | CLI/model: Codex / gpt-6.1-sol | reasoning: <verified or UNKNOWN>
Failure family: <id> | failed corrections: 2 | evidence: <references>

Root Cause: CONFIRMED | HYPOTHESIS | UNKNOWN
- Mechanism: <specific source-level explanation>
- Evidence: <reproducer/log reference/source path:line at examined SHA>
- If unconfirmed: <exact missing evidence, minimal probe>

Required modifications:
1) <repo/path/file.py> :: <function/class>, lines <n-m> at SHA
   Before: <actual behavior>
   Change: <precise alteration to logic/config/data flow>
   After: <expected behavior and why it resolves root cause>
2) <repo/path/test_file.py> :: <test name> — <regression coverage>

Validation: <commands, passing expectations, CI/native gates>
Security/compatibility risks: <bounded effects>
Implementation authority / D6 handoff: <authorized route or NONE>
Usage/cost: <measured or UNKNOWN>
Next action: <Developer assignment / evidence probe / BLOCKED reason>
```

PM must reject a generic “fix the code” report, invented line numbers, unsupported Root Cause or missing exact-file instructions. An honest `RCA_UNCONFIRMED` plus evidence plan is valid *diagnosis*, never evidence that a fix is ready. Record the report in the existing Task/PR/Issue, redact prohibited data, then give an authorized Developer the bounded findings. Only independent cross-family review, exact-head CI and required native/security gates allow merge.
