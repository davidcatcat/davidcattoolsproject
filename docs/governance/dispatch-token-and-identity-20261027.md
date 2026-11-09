# Dispatch-token cost and GitHub identity — bounded implementation notes

Owner decision: 2026-10-27. This note is a technical inventory and next-step acceptance plan; it does **not** claim Token savings or a newly functioning task route.

## Policy reread is not an LLM call

`tools/llmtool/llmtool/worker_dispatch.py` has a local `evaluate()` that reads the selected `worker-dispatch.json` on **each** call, rejects invalid/ambiguous files, parses strict schema and returns an authorization decision with policy SHA-256. This is Python file IO and hash validation; it incurs **no model inference tokens** unless its caller unnecessarily puts the policy text into a model prompt. Do **not** weaken per-dispatch checks or cache an unconditional ALLOWED decision.

Desired boundary:

`PM chooses bounded Task/Worker → local Python dispatch gate → {worker, allowed, reason_code, policy_sha256} → authorized execution or a refusal/receipt.`

- Trace every real dispatch, retry, reassignment, recovery, repair and Reviewer handoff through the same local gate, including direct CLI paths. Verify the checked policy digest is recorded. Unwired routes remain fail-closed or manually checked with their limitation recorded, not represented as integrated.
- Do not send the complete 2+ KiB Owner dispatch JSON or long repeated AGENTS fragments to the LLM solely to make a deterministic authorization decision.
- Measure before claiming savings: count model prompt bytes/tokens for policy context, per-dispatch API/model calls and decision latency, while keeping security evidence redacted. Baselines and saved Tokens are UNKNOWN until a matched test.
- A policy read does not establish model quota, login state, VPN, D6 Writer ownership, Mac readiness or CI status. Those are separate gates.

## Free-model quota failure

Record the Task ID, Writer/session identity, confirmed HEAD and dirty changes, tested scope, most recent successful step, unreconciled side effects, provider error/time, quota status (or UNKNOWN), remaining accepted task budget and next recovery option. A partial draft is not a PASS. If a call or patch effect is UNKNOWN, do not replay it blindly. Before reassigning, verify the previous D6 Writer relinquished control. Paused/Owner-only routes remain paused/Owner-only even if they are technically healthy.

## Current GitHub Reviewer identity

GitHub does not accept a PR author's own native APPROVE as an independent Review. Owner decision 2026-11-04 ([#926](https://github.com/davidcatcat/davidcattoolsproject/issues/926)) identifies `ddavidccatssun` as the AI machine account and `davidcatcat` as the Owner. #926 closed under [Owner decision B](https://github.com/davidcatcat/davidcattoolsproject/issues/926#issuecomment-6697226334): same-user accident guards only, not a security boundary. The AI-account pilot is tracked in #1234 and has not passed. All agents still post as one login, so login identity never proves independence. The present project uses separate read-only Reviewer Sessions, distinct D6 records and strict exact-head formal verdict comments; this is auditable process separation, **not** GitHub-native role separation or tamper-proof enforcement. A second personal GitHub account is not necessary for the existing workflow and should not be requested per PR. A dedicated GitHub App/identity is a **future proposal**, subject to separate security, permissions and Owner authorization, not this change.

## Completion gate

Policy, roster and documentation edits do not implement new transport or lifecycle capabilities. Relevant tests, formal independent review, exact-head Mac/Linux CI and real endpoint receipts are separately required. Track runtime gaps in existing A11c/WP3 rather than creating a second orchestrator.
