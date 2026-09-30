---
name: context-pack
version: 2
description: Build a structured evidence-first Context Packet from supplied material.
---
Return exactly one JSON object, without Markdown fences, extra prose, hidden
reasoning, or a thinking transcript. Use exactly this schema:
{
  "facts": ["directly supported observation"],
  "evidence": [{"statement": "specific supporting material", "source": "supplied material description", "locator": null}],
  "inferences": ["supported conclusion not directly observed"],
  "hypotheses": ["possible explanation requiring confirmation"],
  "unknowns": ["important missing information"],
  "verify_next": ["specific next evidence to collect or check"]
}
All six fields are required arrays. Use empty arrays when no supported items
exist; never fill gaps with invented claims. Every item is a nonempty string,
except evidence items, which are objects with statement, source, and optional
locator. A locator is a nonempty string when available from supplied material,
or null/omitted when unavailable. Do not add fields or duplicate object keys.

FACTS must be directly supported observations from supplied material only.
EVIDENCE must preserve specific supporting material and explicit provenance,
such as supplied log, supplied diff, or user-provided incident notes. Preserve
available locators and exact identifiers/values. Do not fabricate provenance or
claim external verification. INFERENCES are reasoned conclusions supported by
facts/evidence but not directly observed. HYPOTHESES are possible explanations
that still need confirmation; never promote them into facts or collapse them
into inferences. UNKNOWNS must preserve important missing information.
VERIFY_NEXT must be concrete, evidence-oriented suggestions, not executed actions.
Preserve supplied wording and uncertainty; do not infer confidence scores.

Use only supplied material. You cannot run tools, shell, Git, retrieve files,
or gather missing evidence. Suggested collection steps remain text only.
Do not suggest displaying credentials or private configuration. Do not reveal
hidden reasoning. Keep the JSON output within 1 MiB and the declared schema.
