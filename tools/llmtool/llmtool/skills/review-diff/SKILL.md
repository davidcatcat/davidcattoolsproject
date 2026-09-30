---
name: review-diff
version: 1
description: Review issues supported by a supplied diff.
---
Review only issues supported by the supplied diff. Each finding
must include severity (high/medium/low), file and changed line or snippet,
concrete failure/security mode, and a minimal fix. Do not invent surrounding
code. If no supported findings exist, say so and identify evidence limitations.

Use only supplied material. You cannot run tools, commands, Git, or retrieve files.
Do not suggest displaying credentials or private configuration.
