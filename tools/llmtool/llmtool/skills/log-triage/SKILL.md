---
name: log-triage
version: 1
description: Compress supplied logs into evidence signatures and patterns without root-cause or command recommendations.
---
Use only the supplied log material. Return exactly one JSON object without Markdown
fences, extra prose, hidden reasoning, or a thinking transcript. Use exactly this schema:
{
  "summary": "brief evidence-only description",
  "error_signatures": ["distinct supplied error/warning signature"],
  "timeline": ["ordered supplied event when ordering is supported"],
  "repeated_patterns": ["repeated pattern supported by supplied material"],
  "evidence": ["specific supplied log evidence"],
  "unknowns": ["important missing information"],
  "strong_review_reasons": ["reason this requires stronger judgement"]
}

All fields are required arrays except summary. Arrays may be empty. Do not add fields,
confidence scores, severity, root-cause claims, recommendations or commands.

Normalize obvious duplicate messages only when their relationship is supported by the
supplied text. Preserve exact identifiers that matter for correlation. Do not invent
timestamps, request IDs, hosts, pods, services, versions, files, stack frames, counts or
causal ordering. If ordering is incomplete or ambiguous, put that limitation in unknowns.

Do not state a root cause as fact. Do not propose code changes or NEXT_COMMANDS. Do not
declare PASS, SAFE, DONE, VERIFIED, merge-ready or release-ready. If the material touches
security, authentication, authorization/permissions, concurrency/race, data corruption,
or another high-judgement area, add a short item to strong_review_reasons without
resolving it.

Use only supplied material. You cannot run tools, shell, Git, retrieve files, inspect
systems, change code, approve actions or gather missing evidence. Do not reproduce
credential-like values or private configuration if they appear in the supplied material;
replace such values with "[REDACTED]" and preserve only the minimum context needed to
identify the signature. The caller must compare this derived summary with original raw
evidence.
