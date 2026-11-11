# High Debug report — Codex CLI `gpt-6.1-sol`

**Normative Owner decision: 2026-10-27** ([Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6437493012)).

This report is mandatory for a PM-assigned High Debug Task. Follow the [PM Continuation Contract](pm-continuation.md), [Defect Triage](defect-triage.md) and [AGENTS.md](../../AGENTS.md). Do not claim a live `gpt-6.1-sol` invocation unless model/version and runtime evidence are verified.

## Trigger, identity and authority

The trigger is an ordinary repair failure in the same confirmed root-cause/finding family before the 2nd repair, following the [repair cap](pm-continuation-reference.md#repair-cap). High-risk or deep technical uncertainty may trigger earlier when the PM records its evidence. The preferred diagnostic Worker is **`codex-developer` / Codex CLI / `gpt-6.1-sol`**, with high reasoning when supported. Use it only when existing dispatch, model availability, evidence/egress, D6 and task-budget gates permit, in a verified read-only context. Do not purchase or silently substitute a provider or model. A Debug report authorizes no mutation and does not replace independent review.

## Mandatory report

| Field | Required content |
| --- | --- |
| **Identity and source** | Original Task/PR and High Debug Task ID; exact 40-character SHA; actual Worker/provider/model and reasoning; failed-correction count and family; permitted source scope; report date. |
| **Symptom and reproduction** | Error identifiers; exact reproducer/test and expected versus actual behavior; confirmed evidence references; what the two attempted corrections changed and why they failed. Exclude secrets and private logs. |
| **Root Cause — required** | The broken invariant, data/control flow, function call or boundary condition causing the failure. Cite the source and reproducer at the analyzed SHA. Mark **CONFIRMED**, **HYPOTHESIS** or **UNKNOWN** and explain why; do not present inference as proven fact. |
| **Exactly what to modify — required** | Repository-relative paths, functions/classes/config keys, line ranges or unambiguous sections tied to the SHA, what code/logic/configuration must change, why, and expected before/after behavior. Include related callers/tests. If unconfirmed, give conditional candidate locations and the smallest probe, not a fabricated patch. |
| **Verification** | Regression test files/test names; command(s) or safe probe and expected result; affected platforms; required formal Reviewer, exact-head CI and runtime acceptance. |
| **Risks and handoff** | Security/compatibility/performance risks, rollback/containment, bounded implementation scope, responsible authorized Developer and required D6 single-writer transition. |
| **Outcome/cost** | `RCA_CONFIRMED`, `RCA_UNCONFIRMED` or `BLOCKED`; provider/token/cost information or `UNKNOWN`; remaining uncertainties and PM next action. |

### Example format (not actual evidence)

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

PM rejects generic advice such as “fix the code,” invented line numbers, unsupported Root Cause claims or missing exact-file instructions. An honest `RCA_UNCONFIRMED` plus evidence plan is a valid diagnosis, never evidence that a fix is ready. Record the report in the existing Task/PR/Issue, redact prohibited data, then give an authorized Developer the bounded findings. Only independent cross-family review, exact-head CI and required native/security gates allow merge.
