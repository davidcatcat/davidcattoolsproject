# llmtool development-time cost-aware dogfood planning — 2026-10-06

## Scope

Original planning base: `7be031c2f8004143d56116717ada5c8599d47d3f`.
Post-PR #32 integration base: `e5525f18afbb76d457868d18ee90d56bd06b4f66`.

This documentation-only change records an engineering practice that can be used on the
actual Mac with company VPN before A11c is implemented. It does **not** modify production
code, tests, provider configuration, runtime routing, executor/approval behavior or CI.

PR #32 (A11b.1) completed targeted external re-review and merged to main as
`e5525f18afbb76d457868d18ee90d56bd06b4f66`. This branch was then synchronized with
that main using a normal two-parent merge commit. It does not start A11b.2 or claim
A11b/A11c completion.

## Repository findings

- A5 already provides the deterministic local read-only `llmtool git --json` snapshot,
  covering branch/HEAD/status/diff/recent commits. Repeated equivalent Codex `git`
  probes are therefore avoidable when this snapshot contains the required evidence.
- Existing WORKFLOW/ROADMAP already describe company-first preparation and A11c
  cheap-first planning, but they did not clearly separate an immediately usable
  development-time dogfood loop from future production routing.
- Current-state summaries required another sync after the planning branch was created:
  QG-S1/QG-QA1 are PASS/CLOSED and A11b.1 has now merged via PR #32; A11b.2 and A11c
  remain NOT STARTED.

## Added development hierarchy

1. Deterministic/no-model work first: tests, lint/typecheck/build, schema/exit checks,
   A5 Git snapshot, and bounded GitHub/CI state queries.
2. Company LLM through llmtool only for bounded permitted triage: log/test summaries,
   failure clustering, stack-trace extraction, simple classification, baseline
   comparison and checklist presence.
3. Strong verifier for root cause, source changes, architecture, security/auth,
   concurrency, data corruption, test correctness and acceptance/merge decisions.

Raw deterministic evidence remains authoritative. Company-model confidence is not an
approval or correctness signal. Direct company endpoint calls are reserved for explicit
connectivity/runtime comparison, not delegated engineering work.

## Dogfood evidence

Each company delegation should record task type, profile, bounded input scope/bytes,
reported usage tokens or unknown, latency, schema validity, whether the strong developer
adopted/overrode it, and later deterministic/independent verification. These records are
development evidence only and do not create a new A11a diagnostics schema.

## Future A11c requirement

A11c should formalize the same order as policy:
`deterministic/no-model -> authorized company LLM -> strong verifier`.

A future repo/GitHub/CI collector should be deterministic and emit a small structured
state instead of passing verbose `git`/`gh`/CI output to a model. Company-eligible
task classes must be evaluated against fixed fixtures and independent labels before
automatic routing; model self-confidence must not choose the lane.

## Integration order — prerequisite completed

PR #32 was adjudicated and merged first. PR #33 is now synchronized with the resulting
main using a normal non-history-rewriting two-parent merge, and overlapping status text
has been reconciled to keep A11b.1 DONE/merged while A11b.2 remains NOT STARTED.
PR #33 does not modify PR #32's frozen source/test evidence.

## Claude review checklist

Please verify independently that:

1. the current milestone/gate status is accurate, including A11b.1 merged via PR #32 and A11b.2 NOT STARTED;
2. development dogfood does not bypass A11b/A11c gates or imply A11b.2 has started;
3. company LLM output remains untrusted and cannot mutate code, approve merge or declare security safety;
4. existing A5/A6/A9/A10 boundaries are not weakened;
5. deterministic Git/GitHub/test work is kept out of LLM routing when semantic judgement is unnecessary;
6. A11c additions are planning requirements only, not an implemented router;
7. no documentation wording implies measured token/cost savings before usage evidence exists.

No merge is authorized by this report.
