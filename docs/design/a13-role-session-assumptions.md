# A13 Role & Session Assumptions — portability findings

Status: **Verified subset** of the historical provider and session portability findings — not the full mapping.
Pinned main: `d71412f7b5b379b506b049cb2438b31d8fbff952`.
Evidence: #396 comments 6552370022 (OSR-13, OSR-14, OSR-21) and #2793 comment 7210501920.
All facts below were verified by Advisor at pinned main; source identifiers name file and symbol only, no line anchors.

Scope: A13.1 covers project authorization; A13.5 covers user-owned provider, route, and session configuration. Rows are tagged to their owning phase. The broad inventory is documented in `docs/design/a13-platform-assumptions.md` and is not repeated here; no package findings are restated. This note records remaining work only: it proposes no new code, workflow, provider, identity, or release changes, and no new registrations. Source material is a historical subset; session identifiers, machine paths, and account values are deliberately not copied into this document.

| ID | Source identifier (file · symbol) | Verified fact at pinned main | Current assumption | Implication | Owning phase |
|----|-----------------------------------|------------------------------|--------------------|-------------|--------------|
| A13.5-a | `role_config.py` · `resolve_role` | `resolve_role` is the role configuration interface and maps `agy-developer` to `agy`. | Configurable role lookup exists as an interface today. | Recording the existing interface is not a claim that all model or provider defaults are already portable; remaining portability limits are tracked as A13.5 hand-off work. | A13.5 |
| A13.5-b | `review_dispatch.py` · reviewer model selection (via `resolve_role`) | Reviewer model selection resolves through `resolve_role`. | Reviewer model selection already routes through the configurable role lookup. | Role-level lookup is portable; model and provider defaults remain a remaining limit — do not assume full portability. | A13.5 |
| A13.1-c | `worker_dispatch.py` · `WORKERS`; `review_exchange.py` · `run_exchange` (CLI entry point) | `WORKERS` is a finite built-in set of worker IDs. The context step has fixed author and reviewer session comparisons. The CLI still calls `run_exchange`, a path separate from the formal `review_dispatch` flow. | The dispatch consumer set and the exchange session comparisons are fixed at pinned main. | Finite worker set is an extensibility boundary, not a proposal for new registrations. The `run_exchange` consumer remains outstanding work; no new registrations or session rewrites are proposed here. | A13.1 |

Test data rule (Owner #763 A): ordinary tests never contain password-, API-key-, token- or other credential-shaped strings; refusal-flow tests mock, monkeypatch, or inject the screen function and still check real behaviour. Only sensitive-detector tests may use the #981 synthetic-data exception (labelled synthetic, independently reviewed, never real credentials). No production check is disabled, bypassed, or weakened for test convenience. This note is docs-only and changes no tests.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
