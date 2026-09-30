---
name: analyze-log
version: 1
description: Analyze supplied logs and identify supported causes.
---
Analyze the supplied log. Use exactly these sections:
ROOT_CAUSE:
EVIDENCE:
DISTRACTORS:
UNKNOWN_OR_UNPROVEN:
NEXT_COMMANDS:
Do not state a hypothesis as fact. If a cause is unproven, say so.
Do not invent Secrets, namespaces, resource names, credentials, or network root
causes. Cite supplied log evidence. NEXT_COMMANDS must contain at most 3
read-only commands; use explicit placeholders for unknown resource names.

Use only supplied material. You cannot run tools, commands, Git, or retrieve files.
Do not suggest displaying credentials or private configuration.
