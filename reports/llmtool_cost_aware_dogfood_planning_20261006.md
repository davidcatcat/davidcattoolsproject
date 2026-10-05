# llmtool development-time cost-aware dogfood planning — 2026-10-06

## Scope

Base main: `7be031c2f8004143d56116717ada5c8599d47d3f`.

This documentation-only change records an engineering practice that can be used on the
actual Mac with company VPN before A11c is implemented. It does **not** modify production
code, tests, provider configuration, runtime routing, executor/approval behavior or CI.

Draft PR #32 (A11b.1) is intentionally untouched. Its reviewed implementation/report head
and targeted external re-review gate remain separate; this branch must not be used to
start A11b.2 or claim A11b/A11c completion.

## Repository findings

- A5 already provides the deterministic local read-only `llmtool git --json` snapshot,
  covering branch/HEAD/status/diff/recent commits. Repeated equivalent Codex `git`
  probes are therefore avoidable when this snapshot contains the required evidence.
- Existing WORKFLOW/ROADMAP already describe company-first preparation and A11c
  cheap-first planning, but they did not clearly separate an immediately usable
  development-time dogfood loop from future production routing.
- Current-state summaries were stale: QG-S1 had passed with required A11b controls,
  QG-QA1 required blockers were closed and PR #31 merged, and A11b.1 had already moved
  into Draft PR #32 targeted re-review.

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

## Integration order

PR #33 intentionally overlaps PR #32 in repository-guidance/status documentation
(`AGENTS.md`, `CLAUDE.md`, READMEs, ROADMAP and WORKFLOW). That overlap is expected
because PR #33 documents the current A11b.1 state while PR #32 owns the frozen A11b.1
implementation/review evidence.

Do **not** merge PR #33 before PR #32 is adjudicated. After PR #32 is reviewed and
merged or otherwise resolved, synchronize PR #33 with the resulting main using a
normal non-history-rewriting integration, reconcile the overlapping status text, and
re-run the documentation/review gate against the new exact head. PR #33 must not
change PR #32's frozen source/test evidence to avoid the conflict.

## Claude review checklist

Please verify independently that:

1. the current milestone/gate status is accurate;
2. development dogfood does not bypass PR #32 or A11b/A11c gates;
3. company LLM output remains untrusted and cannot mutate code, approve merge or declare security safety;
4. existing A5/A6/A9/A10 boundaries are not weakened;
5. deterministic Git/GitHub/test work is kept out of LLM routing when semantic judgement is unnecessary;
6. A11c additions are planning requirements only, not an implemented router;
7. no documentation wording implies measured token/cost savings before usage evidence exists.

No merge is authorized by this report.
