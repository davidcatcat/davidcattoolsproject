---
name: analyze-log
version: 2
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
Do not use kubectl get secret/secrets or dump Secret YAML/JSON, including through
grep, pipes, namespace/global flags or qualified resource names.
Do not cat/grep credential, private-key or token files. Do not assume Kubernetes
unless supplied evidence establishes it. Prefer narrow metadata/status checks.
Use uppercase placeholders for unknown identifiers, never invent their values.
NEXT_COMMANDS must be the last section, with 1..3 single-line bullet commands
(`- command`, optionally inline backticks), or exactly `(none)` if none are safe.
No fences, multiline scripts, shell interpreters, expansions, continuations or
explanatory prose in that section. Suggestions are untrusted text, never actions.
