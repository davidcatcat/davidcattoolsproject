# A13 OpenCode Route Boundary — syntax, authorization, and runtime dependencies

Status: **Source-grounded note** separating accepted model syntax, actual dispatch authorization, and direct versus Host runtime dependencies.
Pinned main: `4ffd749a95b313591c383b13e1148d80c9704cca`.
Citations: historical OSR-19, from #396 comment 6552370022; #2793, Coordinator comment 7210501920. All code references name file and symbol only, no line anchors. No general platform inventory is repeated.

## 1. Accepted model syntax (adapter) — Advisor verified

Source: `opencode_direct_developer.py` · `is_valid_model`, `validate_model` (historical OSR-19, #396 comment 6552370022).

- `is_valid_model` and `validate_model` check **bounded provider/model syntax**, optionally a variant.
- The free-model helper is a **separate** symbol; validation is **not free-only** — it applies beyond free models.
- Malformed syntax yields `OPENCODE_MODEL_INVALID`.

## 2. Dispatch authorization — what syntax acceptance does not establish

- Syntax acceptance **does not establish that a model is available** and **does not authorize spend**. A passing bounded-syntax check is a gate on string form only; availability and authorization are separate matters and remain unproven here.

## 3. Direct versus Host runtime dependencies

- **Direct Developer use** is a distinct route from the **separate foreground Host credential path**.
- Advisor verified: code references to the native credential reader are in `opencode_foreground_host.py`, **not** in the direct adapter.
- **D8a authority is scoped to the Host path.** The direct adapter carries no credential-reading authority under D8a.
- This note records the dependency boundary only. It gives no credential file locations, schemas, extraction steps, live account data, or instructions.

## 4. Still-required evidence

- User-owned OpenCode availability, subscription, and runtime verification remain **A13.5 / A13.7 evidence still required**; this note does not satisfy them.

## 5. Preservation and non-decisions

- Applicable **worker authorization** is preserved, and the **no paid fallback** condition is preserved.
- **No new model, provider, spend, or release decision** is made by this note.

## 6. Test-data rule (Owner #763 A)

- Ordinary tests never contain credential-shaped strings. Refusal-flow tests mock, monkeypatch, or inject the screen function and still check real behaviour. Only sensitive-detector tests may use the #981 synthetic-data exception: labelled synthetic, independently reviewed, never real credentials. No production check is disabled, bypassed, or weakened for test convenience.

## 7. Limits and gates

- Docs only. No credential reads, no tool execution, no real accounts, no code, CI, or policy modifications.
- Gates: exact-head hosted docs check; formal review by codex-reviewer.
