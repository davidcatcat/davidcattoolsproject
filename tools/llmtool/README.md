# llmtool — Generic LLMTool Core

llmtool is a read-only, provider-agnostic LLM CLI foundation.

這是可攜的通用 LLM CLI，也是未來 orchestration 的底座。目前 production providers
為 `claude-cli`、`codex-cli`、`openai` 與 `openai-compatible`。`claude-cli` 使用本機 Claude CLI runtime，
不是 Anthropic HTTP API provider。`openai-compatible` 可連 self-hosted Qwen、vLLM、相容 gateway、
LM Studio／Ollama 的相容端點，以及其他符合此 request/response 契約的伺服器。
不同伺服器的參數支援仍需確認；不要求它們都支援 Qwen 的 thinking 擴充。

llmtool has no built-in model or deployment endpoint defaults. `openai` uses its fixed official
origin and requires a configured model and explicit API credentials. `claude-cli` requires the
user's local Claude CLI to be installed and authenticated; llmtool does not inspect or manage its
authentication.

通用 template 的 `base_url`、`model` 都是 `null`，不會自動連任何部署。
`openai-compatible` 使用者須設定 endpoint 與 model；`openai` endpoint 固定，使用者設定
model 與明確 API credentials；`claude-cli` 使用已安裝 CLI 的預設 model，或選擇性指定 model。
公司 Qwen 設定僅保留為獨立 deployment example，不會自動載入。

| 概念 | 意義 | 狀態 |
|---|---|---|
| Provider | 如何連模型或 runtime：transport、endpoint、model、auth | `claude-cli`、`codex-cli`、`openai`、`openai-compatible` |
| Profile | 一組具名 provider 設定，例如 `office-qwen`、`home-vllm` | 支援多組及 active selection |
| Skill | 可版本化的 declarative instructions/context | A3 DONE and merged to main |
| Role | workflow 職責，例如 grinder、validator、coder、reviewer | A9 DONE and merged：declarative role registry，與 provider 分離 |
| Agent | A9 AgentStep = role + explicit provider profile + bounded text handoff | A9 DONE and merged；目前無 shell/Git/file/code execution |

目前尚未支援 Anthropic API provider。Claude CLI integration 只作
純文字 runtime request，不使用 CLI tools，不提供 shell、Git 或 code-edit capability。
llmtool 不執行模型回傳的指令。見 [Roadmap](ROADMAP.md)。

## Accepted collaboration and mobile Codex Cloud development lane

[Architecture decisions Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172)
and [Draft PR #173](https://github.com/davidcatcat/davidcattoolsproject/pull/173)
record the owner's D-001, D-002a, D-003a/b/c, D-005a and D-007 decisions:
**ChatGPT Coordinator** discusses product requirements; for complex work the
**existing Codex Integrator** is PM + Integration Manager and decides how to
split and assign agents; **llmtool** performs authorized deterministic
Provider/Worker invocation, policy, state, evidence and events, **not**
complex AI-driven task planning. The local one-time owner handoff -> Worker
completion -> same Integrator wake is an unverified M2 product target.
Company gw remains an API-only advisory Provider with quality- and
egress-gated, task-specific net premium-Token measurements; unknown cost
and usage remain UNKNOWN.

**D-007 mobile parallel development:** while away from the Mac, the owner
can start a separate Codex Cloud task from ChatGPT mobile after the
Cloud environment was configured/published on web/desktop. Coordinator or
Integrator/PM decides which independent, nonoverlapping work package suits
Cloud. Cloud works in its own checkout, implements, tests, **self-pre-reviews**
and, when GitHub permissions allow, publishes its own branch/Draft PR.
**Under the 2026-10-18 handoff disposition, the existing Integrator picks
up the finished PR through actually available, authorized CLI/GitHub
workflows**, verifies the change and obtains independent non-author
Security/Functional/QA plus Mac-only checks as applicable. The
Cloud completion may use optional owner link sharing as a fallback; no
owner completion relay is required. If remote launch is unavailable, record
the concrete task and limitation; a GitHub comment does not prove that Cloud
has started. This handoff is not an llmtool Worker, an automated
original-session callback, or evidence of M2/F2 Live acceptance. Self-review
does not replace independent review. Do not auto-merge main.

Cloud needs repository and push authorization; it does not automatically
inherit Mac checkout, VPN/Keychain or unpushed files. GitHub refs remain
shared between independent Cloud and Mac branches. See
[Workflow](WORKFLOW.md#d-007-independent-mobile-codex-cloud-delivery),
[Roadmap](../../docs/history/llmtool-roadmap-archive-20261028.md#owner-accepted-collaboration-architecture-and-mobile-codex-cloud-lane-2026-10-17)
and [official Codex Cloud guide](https://help.openai.com/en/articles/20001545-using-codex-cloud).

## Roadmap status

- **A0 — Model Validation:** DONE
- **A1 — Generic LLMTool Core:** DONE and merged to `main`
- **A1.1 — Configure Wizard + Live Smoke:** DONE and merged to `main`
- **A2.1 — Provider Capability Foundation:** DONE
- **A2.2 — OpenAI API Provider:** DONE; Responses API, pure text; live API NOT-TESTED
- **A2.3 — Anthropic API Provider:** planned
- **A2.4 — Claude CLI Provider:** DONE; local CLI, pure text; historical pre-hardening live E2E PASS; current status below
- **A2.5 — Codex CLI Provider:** DONE and merged to `main`; external host validation and direct/live E2E PASS
- **A2.6 — Provider Integration / Smoke Tests:** DONE and merged to `main`; host canonical validation 189/189 PASS
- **A3 — Skill Framework:** DONE and merged to `main` (`c9159f2`); external review PASS
- **A3.5 — Interactive Chat Shell:** DONE and merged to `main` (`021456b`); external review PASS
- **A4 — Evidence-first Context Packet:** DONE and merged to `main` (`38b1a67`); external review PASS
- **A5 — Git Intelligence:** DONE and merged to `main` (`7de6a99`); external review PASS
- **A6 — Safety & Validator Layer:** DONE and merged to `main` (`63b5bae`); external review PASS
- **A0–A6:** DONE and merged; A0–A7 bugfix/hardening merged to main (`643caa2`).
- **A7 — Skill Trainer / Feedback Loop:** DONE and merged to main (`d61ca6a`); post-hardening validation and external ChatGPT review PASS
- **A8 — Regression / Benchmark System:** DONE and merged to `main` via PR #9 (`d968161`); external ChatGPT review PASS; A8-R1 FIXED. Claude live model safety benchmark remains FAIL as measured.
- **A9 — Agent Orchestration:** DONE and merged to `main` via PR #10 (`8776476`); A9-R1 FIXED; updated external ChatGPT review PASS; final-head Python 3.9/3.14 CI PASS.
- **A10 — Dynamic Context Retrieval:** DONE and merged to `main` via PR #12 (`21b9757`); independent Claude re-review APPROVE, external ChatGPT final review PASS, final VPN live acceptance PASS, final-head Python 3.9/3.14 CI 605/605 PASS.
- **A11 — Production Workflow & Diagnostics:** **A11a DONE and merged** through PR #26 (`2761ce10`), including final acceptance and B1–B7 post-acceptance regression fixes. **QG-S1 and QG-QA1 are PASS/CLOSED**. A11b is **IN PROGRESS**: **A11b.1 Typed Action & Approval Contract is DONE/merged via PR #32 (`e5525f18`)**; **A11b.2.1 deterministic Git ADD preflight/state verifier is DONE/merged via PR #43 (`780970ab`)** with no `git add` execution or public mutation path. **A11b.2.2.1 synthetic containment is DONE/merged via PR #49 (`eb464628`); A11b.2.2.2 F1 private disposable-fixture-only Git ADD is DONE/merged via PR #84 (`46b17b13`) after independent Security/Functional QA. F2 real-repository mutation remains NOT AUTHORIZED.** **P1 failure-usage preservation is DONE/merged via PR #39 (`56799527`)**. A11c.1 S1a offline inspection is DONE/MERGED via PR #128; S1b Product A fake-only is merged via PR #131; B1 is a candidate with real-provider handoff BLOCKED pending B; full production Integration/Real-world Acceptance remains NOT STARTED and must use route-by-task evidence: only the specific L01 test-triage benchmark pipeline has positive token-saving evidence; ordinary test-triage usage and log-triage/context-pack are not validated production savings routes.
- **A12 — Remote Frontends / Telegram Agent:** future optional frontend; must reuse A11 task/run/event/error/approval semantics and must not create a second execution/logging/error system.

A1.1 code review and live smoke test are complete. It delivers interactive `llmtool configure`,
automatic user config path handling, hidden API
key input, `api_key`／`api_key_env` support, saving incomplete settings as null, atomic writes,
0600 permissions, existing-config protection, profile preservation, and no automatic provider
request during configuration. The live gateway result is recorded in the
[A1.1 smoke test report](../../reports/llmtool_a1_1_live_smoke_20260929.md).

## A11 native self-development — single product roadmap

The product goal is **native llmtool-directed Codex CLI development,
independent Claude CLI review, real tests, bounded repair and safe PR
handoff**, with measured and quality-preserving Token savings when those
routes are genuinely eligible. **A11b/A11c are the only active product
milestones for that goal.** Historical C0–C3 are consolidated back into
[the single A roadmap](ROADMAP.md) and documented only as legacy labels
in [the original proposal](NATIVE_SELF_DEV_MVP.md). Do not implement
a second C executor, QA framework or release line.

The standalone transitional helper is documented in
[tools/ai-loop/README.md](../ai-loop/README.md); it is not an llmtool route.
The requirements owner supplies goals and product acceptance; the AI
team owns technical management, development and evidence-backed review.

**Current native milestone:** A11b.2.2.1 synthetic containment and
A11b.2.2.2 F1 private disposable-fixture-only Git ADD are merged (PR #49,
PR #84 respectively). **F2 user-repository Git mutation remains NOT AUTHORIZED**.
Design the next separate action/approval/containment/rollback contract with
independent Security review first ([Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)).
A11c later performs real Mac Codex/Claude self-development acceptance
and cutover. The merged local-only test-excerpt helper (legacy C1a,
[PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55))
stays available. Its final-output privacy follow-up
([Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66))
must pass before those excerpts go to **any provider**. Company-only
reliability/Token benchmarks belong in A11c; full benchmarks do not
block local A11b design, and other model-bound source/log/diff exports
require their own independent data-boundary review and authorization.

Cost evidence is route-specific: small PR #36 pilot was token-negative
(Codex usage increased by 104.46%); one large-log L01 research pipeline
had two quality-approved Codex savings observations (39.82% / 37.55%).
Do not advertise broad savings before representative tests and accounting.

## A11 delivery checkpoint

A11 is deliberately split into implementation milestones and independent quality
gates so that "code reviewed", "bugs searched", "security assessed" and "QA passed"
are never treated as the same claim.

| Stage | State | Purpose |
| --- | --- | --- |
| A11a — Observability & Diagnostics | **DONE** | Structured diagnostics, opt-in safe metadata store, runs/trace/doctor, failure handling |
| QG-S1 — Security Baseline / Threat Model | **PASS/CLOSED** | Assess current attack surface and pin security requirements before executor work |
| QG-QA1 — Formal A11a QA | **PASS/CLOSED** | Exercise public CLI behavior, failures, concurrency/state and supported environments |
| A11b — Execution, Approval & Recovery | **IN PROGRESS — A11b.1 PR #32, A11b.2.1 PR #43, A11b.2.2.1 PR #49 and sealed F1 PR #84 DONE/merged** | F2 real user-repo mutation NOT AUTHORIZED; next separate Security-reviewed F2 and A11b.3 approval design |
| A11c — Integration & Real-world Acceptance | **S1a DONE/MERGED; S1b A MERGED; B1 candidate** | S1a product accepted via PR #128; S1b contract merged via PR #129; B production attestation/grant/route gates remain required; full production integration/real task acceptance NOT STARTED; no blanket company-first policy |

### Home LLM routing evidence after Round 3

Round 3 is closed as an experiment record; PR #38 is merged. P1 from PR #39 is also
merged and preserves a validated `prompt_tokens/completion_tokens/total_tokens` triplet
on parseable provider failures without changing failure semantics.

Current routing evidence:

| Route | Evidence state | Current policy |
| --- | --- | --- |
| `test-triage` | **BENCHMARK PIPELINE VALIDATED; ordinary skill use UNVALIDATED for savings** | Positive evidence requires the frozen L01-style pipeline: ~48 KiB canonical raw output, complete FAILURE-ID coverage, deterministic ±2-line excerpt selector capped at `min(32 KiB, 20%)`, inline selected raw, and no full-raw re-read by Codex |
| `log-triage` | **EXPERIMENTAL** | May assist bounded triage, but do not claim token savings or authorize A11c routing from current evidence |
| `context-pack` | **EXPERIMENTAL** | May assist bounded evidence normalization, but Round-3 3/7 target recall and instruction/gate mismatch must be redesigned before routing claims |

The two quality-approved L01 observations reduced Codex usage by 39.82% and 37.55%
under opposite run order on the same synthetic fixture. They are not pooled into a
generalized savings rate and do not validate plain `llmtool ask --skill test-triage`.
Round-1 B01 used small bounded evidence plus a Codex tool read and measured −103.32%;
its first default-budget attempt also hit `MODEL_OUTPUT_INCOMPLETE`.

A new delegation route must pass a pre-registered **Company-only reliability Phase 0**
before any paired Codex benchmark is authorized. A failed or incomplete Company-only
gate stops the paired benchmark. Round 4 remains optional/deferred research and does
not block A11b.2.

A11a final acceptance merged through PR #25 (`e3869828`). The independent full bug
hunt then found seven confirmed diagnostics defects; Codex reproduced each one,
added permanent regression coverage and fixed B1–B7 in PR #26 (`2761ce10`).
Claude independently re-ran the original probes and approved the fixes. Post-merge
CI `37228032137` passed Python 3.9/3.14.

A11a remains read-only/text-only apart from explicit trusted metadata persistence:
no executor, hooks engine, automatic retry/fallback, patching or Git mutation.
Persistence defaults off, and diagnostics must remain secondary to the primary
task. Historical reports retain their original pre-merge status wording.

The merged [QG-S1 security report](../../reports/llmtool_qg_s1_security_baseline_20261005.md)
passed with required future design controls. DG-1 lifecycle fixes and independent
re-review merged through PR #29. [Formal QA round 2](../../reports/llmtool_qg_qa1_targeted_rereview_round2_20261005.md)
closed Q-BUG-1, F1 and D-34b with APPROVE; PR #31 merged as `7be031c2`.
QG-S1 and QG-QA1 are PASS/CLOSED. Historical FAIL/pending wording remains intact.
A11b.1 is DONE/merged via PR #32 (`e5525f18`) as a data-only contract slice.
A11b.2.1 is DONE/merged via PR #43 (`780970ab`) as an internal read-only Git ADD
preflight/state verifier; it does not execute `git add` and is not a public mutation path.
A11b.2.2.1 synthetic process-containment harness is DONE/merged via PR #49 (`eb464628`),
with independent Security/Functional QA PASS, Mac 14/14 native tests and SIGKILL 20+20.
This slice did **not** implement actual Git ADD or a public mutation surface.
A11b.2.2.2 requires separate reviewed product integration design and authorization.
Track S49-3/QA49-N2 raw-FD cleanup, QA49-N1 natural leader-exit regression and
P49-L1 fault hook isolation before real executor integration. See the
[B5 self-anchor amendment](../../reports/llmtool_pr49_b5_self_anchor_amendment_20261008.md).
A11b as a whole is incomplete. Future executor work must still meet QG-S1
R-EXEC/R-FS/R-GIT/R-APR/R-INJ/R-EXP/R-AUD.

Detailed milestone scope and retained limitations are in [ROADMAP](ROADMAP.md).
Engineering execution/review rules are in [WORKFLOW](WORKFLOW.md). Root repository
skills distinguish ordinary code review, bug hunting, security review and QA; those
are separate evidence gates, not interchangeable labels.

## Canonical repository workflow

Local Mac work uses `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
When the user chooses Codex Cloud, use only the repository checkout configured for
that Cloud task; discover its actual root instead of assuming `/workspace` is a
Git repository. Verify repository identity, branch, HEAD and working-tree state.
Owner D6 dynamic, registered, task-scoped worktrees are the current local
development workflow. Follow [safe-git's Owner D6 worktree guidance](../../skills/safe-git/SKILL.md#owner-d6--dynamic-task-scoped-worktrees)
and the [D6 worktree lifecycle](../../skills/safe-git/worktree-lifecycle.md)
for registration, ownership and lifecycle requirements. Do not create
independent clones or sibling/nested repositories. Cloud tasks continue to use
only their configured checkout. F1 native acceptance remains canonical-only;
F2 Git mutation is not authorized. If ownership, state or contents are uncertain,
STOP and preserve the worktree.

Follow the [llmtool development workflow](WORKFLOW.md) for Cloud development,
Mac-required verification, evidence handoff and merge gates. Complete independent
Cloud work before handing off checks that require the user's Mac. Mock/CI success
does not replace required live verification. Preserve unrelated user work and
stop at the milestone's review gate; do not automatically merge or start the next milestone.

If Git writes are blocked, leave changes in the authorized checkout and provide
exact commands for that environment; do not create another checkout.
Test-generated temporary files and the `codex-cli` provider's empty ephemeral
runtime cwd remain allowed security/test mechanisms, never project checkouts.
This development policy does not change llmtool provider capabilities or runtime isolation.

## A2.6 integration validation

All four implemented providers coexist as named profiles under the same registry,
configuration schema, selection, configure flow, CLI commands and plain-text `Reply`
contract. Provider != Role != Agent. Anthropic API, roles, agents, orchestration and
automatic command execution remain outside A2.6.

Shared integration tests cover mixed profiles, user → environment → explicit CLI
selection precedence, selection without saved-config mutation, provider-specific
field rejection, redacted config display, deterministic capability output, real
response normalization behind mocked transports, and configure without runtime I/O.
The configure summary now uses the selected active profile's type when displaying
an endpoint, including when a different profile was edited.

Current validation: 11 new integration tests PASS; expanded targeted suite 148 PASS.
User-reported host canonical result:

```text
Ran 189 tests in 23.278s
OK
```

All 189 canonical tests passed. The previous sandbox loopback-binding limitation
was environment-specific; host validation resolved the remaining canonical gate.
No implementation changes were needed after the host run. A2.6 implementation +
canonical validation are **complete**; A2.6 is merged to main. A2.7 remains outside this milestone; A3 status is documented below.
Live smoke remains: `openai-compatible` NOT-TESTED; `claude-cli` attempted,
unsuccessful; `codex-cli` attempted, unsuccessful; `openai` NOT-TESTED.
Historical provider smoke passes are not evidence of a new A2.6 live pass. See the
[A2.6 validation report](../../reports/llmtool_a2_6_provider_integration_validation_20260930.md).

## Provider capabilities

`llmtool providers` lists the capabilities declared by each production provider implementation;
`llmtool providers --json` prints the same metadata as deterministic JSON. Both commands are
offline introspection: they do not read user/project config or credentials, create a config file,
or connect to a provider.

Capabilities describe technical implementation/runtime ability. **Capability is not authorization,
policy, or approval.** In particular, `code_edit`, `shell`, or `git` metadata does not grant
permission or cause llmtool to modify files, execute commands, or operate Git. Capability metadata
is implementation-owned and is not stored in user/project config.

The `openai` provider declares `chat: yes`, `large_context: yes`, `reasoning_control: yes`,
`streaming: no`, `code_edit: no`, `shell: no`, and `git: no`. The `openai-compatible` provider
declares the same capabilities except `streaming: yes`.
`claude-cli` and `codex-cli` declare only `chat: yes`; their `large_context`, `reasoning_control`, `streaming`,
`code_edit`, `shell`, and `git` capabilities are false.
`large_context` describes support for large-context workflows, not a fixed context window;
deployment/model limits vary. `reasoning_control` describes this implementation's optional
`provider_options.enable_thinking` extension, sent as `chat_template_kwargs.enable_thinking`.
That field is not a universal OpenAI-compatible standard, and endpoint support varies.
The dedicated `openai` provider instead maps `provider_options.reasoning_effort` to Responses API
`reasoning.effort`; supported values can vary by model.

## OpenAI API provider

The `openai` provider uses the fixed official HTTPS origin `https://api.openai.com` and sends
plain-text conversations to `POST /v1/responses`. It sends `max_output_tokens`, never sends
`temperature`, explicitly sets `store: false`, and does not enable streaming, tools, code editing,
shell, or Git. Optional
`reasoning_effort` values are `none`, `low`, `medium`, and `high`; model support varies. The
provider returns only final assistant text and safe response metadata, never reasoning items.

Example user config (set the environment variable yourself; llmtool does not discover credentials):

```yaml
providers:
  openai-main:
    type: openai
    model: "your-model"
    api_key_env: OPENAI_API_KEY
    provider_options:
      reasoning_effort: medium
active_provider: openai-main
generation:
  max_tokens: 2000
```

Do not set `base_url` for `type: openai`; alternate endpoints are rejected. OpenAI API live usage
requires separately configured OpenAI API credentials and billing. ChatGPT subscriptions and API
billing are separate; tests do not require live usage, billing setup, or credits. The OpenAI
provider has not been validated against the live API; provider tests use a local mock server.

## Claude CLI provider

The `claude-cli` provider invokes the user's installed `claude` executable as a local runtime; it
does not call the Anthropic HTTP API. It sends the llmtool conversation over stdin using the CLI's
non-interactive print mode with text output, safe mode, no session persistence, and all tools
disabled. The subprocess uses an argv list, bounded output, a process-group timeout, an allowlisted
environment, and an empty temporary working directory; raw CLI stderr is never forwarded. Its
resolved executable must be an absolute path. It does not edit files, execute shell commands,
operate Git, stream, or expose reasoning controls. The CLI's default model is used unless a model is
configured. Generic temperature and
max_tokens settings are retained in config but are not sent or enforced by Claude CLI.

Claude CLI must already be installed and authenticated by the user. llmtool does not discover,
read, or manage Claude credentials and does not configure CLI authentication. `llmtool configure`
does not ask for a Base URL or API key and does not start Claude CLI. This runtime provider is
separate from the Anthropic API provider, which remains planned.
The provider resolves `claude` from the llmtool process `PATH`; PATH order determines which
installed executable and version are used. Project config cannot select or override the executable.
The historical pre-hardening Claude CLI provider's live end-to-end smoke passed in the normal authenticated user shell;
the Codex isolated runtime may resolve a different executable and does not establish the same
authentication state. See the [runtime validation report](../../reports/llmtool_claude_cli_runtime_diagnosis_20260929.md).
Current hardened implementation: offline/fake-runtime tests PASS; current marker-only
live E2E PASS with Claude CLI 2.1.286 (`CLAUDE_HARDENED_OK`, exit 0). This smoke
exercised executable resolution, the environment allowlist, isolated cwd, process lifecycle,
and final-text normalization. See the [Round-2 validation report](../../reports/llmtool_a0_a7_bugfix_validation_20261001.md).
For isolated llmtool configuration tests, `LLMTOOL_CLAUDE_CLI_HOME` can point the child process
at the user's existing Claude CLI home while llmtool itself uses a temporary `HOME`; it is a
runtime environment setting and cannot be set in project config.

Example user config (the model is optional; the CLI default is used when omitted):

```yaml
providers:
  local-claude:
    type: claude-cli
    model: sonnet
    timeout_seconds: 120
active_provider: local-claude
```

## Codex CLI provider

`codex-cli` is a pure-text runtime transport, separate from roles and agents. It resolves
`shutil.which("codex")` on every request; PATH determines executable/version. Neither user nor
project provider config can specify an executable. The inspected installation is Codex CLI
**0.159.2**. Authentication belongs entirely to the existing Codex CLI login/session: llmtool
never reads auth files, accesses keychain credentials, manages login, or copies credentials.

```yaml
providers:
  local-codex:
    type: codex-cli
    # model: your-model  # optional; Codex chooses its default when omitted
    timeout_seconds: 120
active_provider: local-codex
```

Select it with `llmtool ask --provider local-codex 'your question'`, or make it active in user
config. `llmtool configure` skips HTTP endpoint and API-key prompts for this type. Non-null
`base_url`, `api_key`, and `api_key_env` are rejected. `provider_options` may contain only
`reasoning_effort`, with one of `low`, `medium`, `high`, or `xhigh`; it maps to the CLI setting
`model_reasoning_effort`. Executable, cwd, approval, sandbox, and arbitrary CLI fields are
rejected. Model identifiers must match `[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`.
Temperature/max_tokens are stored but neither
sent nor enforced. Only `chat` is true: large-context workflows, streaming, code editing, shell,
and Git are not exposed. Model window size alone does not establish large-context workflow support.

Each request creates an empty, private temporary cwd under `/tmp` (fallback `/var/tmp`), outside
the caller's repository. Inherited `TMPDIR` cannot redirect it into the project. Both subprocess
`cwd` and `--cd` select that directory; nothing from the caller repository is copied or mounted.
Prompts travel through stdin, never prompt files. `exec` uses `--no-daemon`, approval `never`,
`--ephemeral`, `--skip-git-repo-check`, `--ignore-user-config`, `--ignore-rules`, and strict config.
`project_doc_max_bytes=0` suppresses AGENTS.md instructions. The argv fixes a named permissions
profile, `llmtool-text`, extending `:read-only`, denying root/temp filesystem reads, reopening
only Codex's minimal system paths and the isolated workspace for reads, and disabling tool
network access. No `--sandbox` flag is added: it would override the stronger permission profile.
See [Codex permission profiles](https://learn.chatgpt.com/docs/permissions) for this precedence.

Documented shell/unified-exec, shell snapshots, multi-agent, hooks, apps, memories, remote-plugin,
and code-mode features are disabled; web search is disabled. These are defense in depth, not a
claim that every Codex tool is disabled. **Isolation plus the restricted read-only sandbox form
the execution boundary.** A malicious executable selected by PATH is outside this boundary:
the installed CLI itself is trusted and handles authentication/model networking outside its
tool sandbox. System/managed Codex policy and future CLI changes also remain runtime dependencies.
The external host validation passed for Codex CLI 0.159.2; deployment hosts and future
CLI versions still depend on their effective runtime policy.

The child receives an allowlisted environment: PATH, HOME, USER, LOGNAME, locale settings,
CODEX_HOME, and TLS certificate paths when present; TMPDIR becomes the isolated cwd. Other
variables, including API keys, Git overrides, Node startup injection, and remote endpoints,
are not inherited. HOME/CODEX_HOME preserve the user's existing CLI session location; when
using an isolated llmtool HOME for testing, explicitly set CODEX_HOME to the existing Codex
state directory without inspecting its contents. User-config-dependent custom gateways/proxies
are not supported by this first version. Global CLI-managed skill/runtime state may still be
loaded by Codex; the caller repository is not made available as context.

The POSIX subprocess uses an argv list, `shell=False`, closed extra descriptors, a separate
process group, nonblocking stdin/stdout/stderr, a deadline, and a separate 1 MiB bound on each
output stream. Timeout/overflow kills the process group. Errors omit stderr, prompts, secrets,
and environment values; invalid UTF-8, empty output, nonzero exit, and startup failure are
normalized. Default text mode returns only final stdout; internal stderr diagnostics are
captured and discarded, never forwarded. Reasoning summaries are suppressed. Temporary cwd
cleanup runs on success/failure; CLI-owned authentication/runtime state is not deleted.

Claude/Codex CLI invocations preserve inherited `SIG_IGN` for SIGINT, SIGTERM and
SIGHUP, including nohup/background-job ignores. For other dispositions, the first
observed signal drives coordinated cleanup of the owned process group and temporary
cwd. Signal handlers only record cancellation; the provider loop performs cleanup.
SIGINT retains the existing interruption contract; TERM/HUP cancellation during an
active invocation exits 143/129 (`128 + signal`), without provider stderr or a traceback.
Signals observed while scoped handlers are installed retain the first cancellation.
Repeated signals after restoration may use the normal OS disposition; process-tree
and runtime cleanup is complete before restoration on the supported cleanup path.
This synchronous lifecycle requires the main thread. Default SIGCHLD reaping is
retained; inherited SIGCHLD ignore is temporarily changed to default and restored
after cleanup. Custom SIGCHLD handlers are rejected before spawn so an unreaped
owned child pins its process-group identity.
It does not contain a trusted child that deliberately detaches with setsid (TR-3),
and cannot clean up after SIGKILL or a host crash.

Tests and observed live results are in the [A2.5 validation report](../../reports/llmtool_a2_5_codex_cli_validation_20260930.md).
**External host-terminal validation PASS:** macOS sandbox startup, absolute caller file/Git/AGENTS.md
read denial, direct `CODEX_DIRECT_OK`, and llmtool E2E `CODEX_CLI_OK`; cleanup left no inference-caused
mutation. The temporary HOME-level AGENTS.md did not contaminate the tested invocation with real
CODEX_HOME retained. This does not prove arbitrary CODEX_HOME runtime/auth state or every future
CLI version is isolated. Existing Codex authentication/runtime and the PATH executable remain
trusted dependencies. A2.5 is merged to main; A2.6 implementation and canonical validation are complete (see the A2.6 section).

## 安裝與直接執行

需要 Python 3.9+，支援 macOS/Linux。從 repo 根目錄：

```sh
python3 -m venv tools/llmtool/.venv
tools/llmtool/.venv/bin/python -m pip install -e tools/llmtool
tools/llmtool/.venv/bin/llmtool --help

# 啟用環境後可直接使用 llmtool
. tools/llmtool/.venv/bin/activate
llmtool --version
```

也能在安裝相依套件的 Python 環境直接執行：

```sh
python tools/llmtool/llmtool.py --help
# 或在 tools/llmtool 內執行 python -m llmtool
```

唯一執行期 dependency：`PyYAML>=6.0,<7`，使用 SafeLoader 解析 YAML，
避免自行實作不完整的 YAML parser。HTTP、CLI 與測試使用 Python 標準函式庫。
setuptools 僅用於打包。離線環境需事先備妥套件 wheel／內部套件來源。

Console, module and direct launchers share a thin startup entrypoint. Once inside
its guard, Ctrl-C during CLI import, parser construction or argument parsing returns
130 with fixed `llmtool: interrupted` stderr. Python interpreter/site initialization
before that boundary remains a platform limitation; it is not controlled by llmtool.

## 設定

```sh
llmtool configure                  # 互動式設定精靈，不需要手動找 config path
llmtool config path                 # ~/.config/llmtool/config.yaml
llmtool config init                 # 新檔權限 0600，既有檔案不覆蓋
llmtool config init --force         # 明確要求才取代既有檔案；拒絕 symlink
llmtool config show                 # resolved YAML，API key 遮罩
llmtool config show --max-tokens 4000
llmtool config show --provider home-vllm  # 先在 user config 建立該 profile
```

`configure` 會依 provider 詢問設定；OpenAI 使用官方 endpoint，不詢問 base URL，驗證後寫入
user config，不會連線測試 provider。API key 使用不回顯的終端輸入；也可只填 API key
環境變數名稱，兩者不可同時設定。初次設定的空 optional 欄位保持 unset；編輯時留空保留
既有 profile 欄位／provider_options、generation 和 active_provider。新增 profile 不自動切換 active provider。
optional 欄位可輸入 `-` 清除；hidden API key 留空保留、輸入 `-` 清除，既有 secret 不顯示為 default。
明確更換 provider type 會清除不相容的舊 transport 欄位。OpenAI API key env 初次設定預設提示 `OPENAI_API_KEY`，只是
config 欄位建議值，不會在 configure 時讀取環境變數。新檔自動建立目錄並以 `0600` 權限原子寫入。
若設定檔已存在，精靈先詢問是否更新，預設為 No；確認更新會保留其他 provider profiles。
精靈需要互動終端，確保 API key 不會退回可回顯輸入。

`config init` 仍只產生 generic template。`openai-compatible` 需設定 `base_url` 與 `model`；
`openai` 使用固定官方 endpoint，不接受自訂 `base_url`，需設定 model 和明確的 API key 或
key 環境變數名稱；`claude-cli` 使用已安裝並由使用者登入的本機 Claude CLI，不設定 HTTP
endpoint 或 llmtool API credential。之後再執行 `ask` 等模型命令。
`config show` 可以顯示尚未填妥的 null 設定；模型命令則在讀取 credential／發出 HTTP
之前失敗，訊息指出所選 provider 缺少必要的 endpoint、model 或 authentication 設定。
未選用的 profile 可保持 null，不影響已完整設定的 active provider。

User config 固定是 `~/.config/llmtool/config.yaml`（依 HOME；不使用 XDG_CONFIG_HOME）。
Project config 為**目前工作目錄**的 `.llmtool.yaml`；不搜尋父目錄、不讀 Git metadata。
兩者皆非必需。各來源只對允許的欄位套用優先順序：

```text
CLI option > environment > project config > user config > built-in defaults
```

Generic **user config template** 見 [config.example.yaml](config.example.yaml)，
內容與 built-in defaults 一致：

```yaml
providers:
  default:
    type: openai-compatible
    base_url: null
    model: null
    timeout_seconds: 120
    api_key: null
    api_key_env: null
    provider_options: {}
generation:
  temperature: 0
  max_tokens: 2000
active_provider: default
```

### 手動配置的 deployment examples

公司部署範例見 [config.example.company-qwen.yaml](config.example.company-qwen.yaml)。
只有確認要使用該 endpoint 時，才手動把相關欄位填入 user config；程式不會搜尋或讀取
這個範例檔。以下 IP／model 也只是範例，並非 generic 預設值：

```yaml
providers:
  office-qwen:
    type: openai-compatible
    base_url: "http://192.0.2.10:4000"
    model: "gw"
    provider_options:
      enable_thinking: true
  home-vllm:
    type: openai-compatible
    base_url: "http://192.168.1.20:8000"
    model: "qwen"
active_provider: office-qwen
```

新 profile 在發出模型請求前必須定義 endpoint、model；其他欄位使用中立預設（type 為
`openai-compatible`、timeout 120、無 auth、空 provider_options），**不繼承**
`default` 的 endpoint、credentials 或 thinking 選項。Provider 名稱長 1–64 字元，
以英數字開頭，可包含英數字、`-`、`_`。選擇不存在的 profile 會報錯。

`openai` profile 不使用 `base_url`，並在模型請求時要求 model 和明確設定的 API key 或
`api_key_env`。`OPENAI_API_KEY` 只會在使用者將它指定為 `api_key_env` 後讀取。

### Codex direct 執行時間上限（user config）

`codex_direct.timeout_seconds` 只能寫在 user config（`~/.config/llmtool/config.yaml`），專案 `.llmtool.yaml` 不能設定，也不受 `active_provider` 或專案設定錯誤影響（AUDIT-027，#603）。預設 600 秒，上限 1800 秒；必須是正整數，超出範圍、0、負數、小數、字串或布林值會以 typed 設定錯誤拒絕，不會自動夾到範圍內。Codex 任務封包的 budget、prompt 與實際 timeout 使用同一個值。

```yaml
codex_direct:
  timeout_seconds: 900
```

### User-only accounts and roles

Optional `accounts` and `roles` live only in `~/.config/llmtool/config.yaml`.
An account names a Claude or Codex CLI configuration directory; a role can select
an account and optionally specify a provider profile, model and effort. Account
directories must already exist, be real directories owned by the current user,
and not be writable by the group or others. llmtool checks directory metadata
only and never opens credential files. Project `.llmtool.yaml` cannot set either
section.

```yaml
accounts:
  work-claude:
    kind: claude
    config_dir: /home/example/.config/claude-work
roles:
  reviewer:
    account: work-claude
    provider_profile: default
    model: example-model
    effort: high
```

`resolve_role(role, cli=None, environ=None)` resolves one role with one-shot CLI
overrides above role-scoped environment overrides such as
`LLMTOOL_ROLE_REVIEWER_ACCOUNT` and `LLMTOOL_ROLE_REVIEWER_MODEL`, saved role settings,
the legacy `pm_runtime.claude_config_dir` mapping for `pm`, and neutral built-in
defaults. Overrides are returned in memory and never saved. Existing
`pm_runtime.claude_config_dir` remains supported; when both it and `roles.pm`
select account directories, they must agree. Account directory metadata is checked
when the selected role uses the account. General `llmtool config` output does not
include account or role configuration.

To set up a PM in the TUI (Claude account folder, Codex runtime, working
directory and default PM), read the [PM setup guide](../../docs/guides/llmtool-pm-setup-zh-tw.md)
and the [offline acceptance pack](../../docs/guides/llmtool-codex-pm-offline-acceptance-zh-tw.md).

### Project config 的低信任邊界

`.llmtool.yaml` **只能**修改已由 user/default 定義的 provider 的 `model`、
`timeout_seconds`，以及 `generation.temperature`、`generation.max_tokens`。
不能新增 provider、切換 `active_provider`、指定 type、base_url、api_key、api_key_env
或 provider_options。禁止欄位即使值為 null、等於目前值、或被 CLI/env 覆蓋，也直接報錯。
完整 project schema 在讀取 credential 環境變數／建立 HTTP client 之前檢查，
錯誤不回顯 secret、原始值或攻擊者指定的欄位名稱。

合法 project config：

```yaml
providers:
  default:
    timeout_seconds: 180
generation:
  max_tokens: 4000
```

不要把完整 `config.example.yaml` 複製為 project config，它包含被禁止的欄位。

| Source | 可控制欄位 |
|---|---|
| User config | Named providers、provider-allowed endpoint、auth、type、provider_options、active_provider、generation |
| Project config | 既有 profile 的 model/timeout，以及通用 temperature/max_tokens |
| Environment | 下表列出的明確變數，套用到選定 profile |
| CLI | 下表列出的明確 flags，套用到選定 profile |

| CLI（放在子命令之後） | Environment | YAML |
|---|---|---|
| `--provider` | `LLMTOOL_PROVIDER` | `active_provider` |
| `--base-url` | `LLMTOOL_BASE_URL` | `providers.<selected>.base_url` |
| `--model` | `LLMTOOL_MODEL` | `providers.<selected>.model` |
| `--timeout` | `LLMTOOL_TIMEOUT` | `providers.<selected>.timeout_seconds` |
| `--max-tokens` | `LLMTOOL_MAX_TOKENS` | `generation.max_tokens` |
| `--temperature` | — | `generation.temperature` |

`openai-compatible` 的 `base_url` 只接受 HTTP(S) origin，例如 `http://host:4000`，不包含
`/v1`、credentials、query 或 fragment；工具固定附加 `/v1/chat/completions`。`openai` 不接受
user/project/CLI/env 提供的 `base_url`，固定使用官方 HTTPS origin 與 `/v1/responses`。
`timeout_seconds` 必須大於 0 且不超過 3600 秒；這是 provider runtime/socket timeout，不是
保證的端到端牆鐘上限。`api_key` 必須是沒有空白的可列印 ASCII；`max_tokens` 為正整數；
temperature 為 0–2。
未知欄位、重複 key、YAML aliases、錯誤型別與 malformed YAML 均報錯。

先以 CLI > env > user > default 決定 active profile，再把 CLI/env 的 model 等允許覆寫套到
這一組；不更動其他 profile。`openai` 會拒絕 endpoint override。尚未接受 roles／command／
agent routing 設定。

預設無 auth。未設定 key 時不送 Authorization；需要時可擇一設定
`api_key` 或 `api_key_env`。後者只查明確命名的環境變數，缺值時請求失敗。
不讀 `.env`、credential store、keychain 或 SSH 檔案。不要把 key 寫進命令列。
合併後兩個 auth 欄位不能同時有值；覆蓋 auth 方式時將另一個明確設為 `null`。

`config show` 遮罩**所有** profile 的 key，並不解析 api_key_env 的值；模型請求
只解析選定 profile 明確指定的 key env。未選定 profile 的環境 secret 不會讀取。
User config、環境變數與明確 CLI 是信任來源：`--base-url`／`LLMTOOL_BASE_URL`
會將選定 profile 的 key 傳到使用者指定的目的地，請只使用信任的 endpoint。
Project 雖不能改 endpoint/auth，仍可在同一 endpoint 選 model；不適合當作 model ACL。

`.gitignore` 保留 `.llmtool.yaml`、Python cache，其他 artifact 僅忽略
`tools/llmtool/` 的 `.venv/`、`build/`、`dist/`、`*.egg-info/`。
不再全 repo 忽略 `config.yaml`／`config.yml`；真實 user config 位於 repo 外，
不要將其複製進 repo。Repo 只追蹤無 secret 的 example。

### 從最初 A1 config 遷移

舊 `providers.company` 現在只是一般 profile 名稱，不再隱含選用。
可改名為 `default`，或明確設定 `active_provider: company`。
把 `generation.enable_thinking` 移到對應 profile 的
`provider_options.enable_thinking`；舊的通用欄位會報錯，不默默忽略。
將 project config 的 endpoint、auth、type 和 active selection 移至 user config。
不會自動改寫既有設定檔。
先前若依賴內建 endpoint/model 而沒有寫入 user config，現在需明確填入兩者；
`config init --force` 也只會重建 null template，不會恢復任何部署預設值。

## 四個命令

```sh
llmtool ask "why did this fail?"
llmtool ask "解釋這個錯誤" --max-tokens 3000 --timeout 180
llmtool ask "why did this fail?" --provider home-vllm

llmtool analyze-log --file app.log
cat app.log | llmtool analyze-log

git diff | llmtool review-diff > review.txt
llmtool review-diff --file change.diff

cat incident.txt | llmtool context-pack > context.txt
llmtool context-pack --file incident.txt --verbose
llmtool context-pack --json --file incident.txt
```

### Submit a development request to a selected PM

```sh
llmtool task submit --provider my-pm "Add a health-check command to the service CLI"
llmtool task show 0123456789abcdef0123456789abcdef
```

`task submit` (and `/task submit` in `chat --tui`) is the developer/test intake
path — 開發測試用：只登記，不會派工: it records a Task and dispatches nothing. The
product entry for talking to the PM is the dedicated Claude Code PM (`/pm new`,
`/pm connect` in `chat --tui`). `task submit` requires an explicitly configured
provider profile; it never picks a default or falls back to another provider. It creates a local Task, Run and logical
PM Session record before calling the provider. The returned Task ID can be used
with `task show` in a later CLI process. Records live under
`~/.local/state/llmtool/tasks` with owner-only directory/file permissions. Request
and reply text with recognizable credential material is rejected before it can be
sent or persisted. A failed or unconfigured provider leaves a `PM_UNAVAILABLE`
record with a fixed error code and no secret-bearing diagnostics.

The PM Session ID is llmtool's logical binding for this intake. The current Codex
Provider uses one ephemeral CLI call and has no native resumable Session ID;
`task show` retrieves the recorded Task and response but does not resume a model
conversation, dispatch a Developer, run tests, or continue work in the background.

The dedicated PM (`/pm new`, `/pm connect`) runs `claude` with the inherited environment
unless the trusted user config `~/.config/llmtool/config.yaml` sets
`pm_runtime: {claude_config_dir: /Users/me/.claude-account2}` (an absolute path; project
`.llmtool.yaml` cannot set it). pm_runtime then starts the PM with that directory as
`CLAUDE_CONFIG_DIR` and looks up the session transcript there; an invalid value refuses to
connect with `PM_CONFIG_INVALID`. `/pm connect` and `/pm` show the Claude account
directory in use. The TUI PM runs in Claude Code auto permission mode
(`--permission-mode auto`, Owner #304 c6494914495); the stop-loss is to revert to
chat-only after any action outside the Owner's authorization.

`task record-pm --stdin-json` records a decision from the active PM runtime without invoking a
provider. Its strict JSON input contains `request` and a structured `response`; it accepts only
LOW-risk `opencode` or `codex` Developer assignments. The record identifies its runtime as
`active-pm-runtime` and does not claim a Codex or Claude native session ID.
A Claude Code PM adds `--pm-session-id SESSION_UUID` (and `--pm-cwd DIR`, default the
current directory). llmtool refuses the record unless this user's Claude Code transcript
for that session and directory exists (under `$CLAUDE_CONFIG_DIR` or `~/.claude`), with the
configuration, `projects` and project directories and the file all owned by the user
and none of them a symlink. It then
stores the PM session as `pm-SESSION_UUID`, records `pm_runtime: claude-code`, the session
UUID and directory, and `task show` lists `pm_recovery` (`claude --resume SESSION_UUID`
in that directory). The file check shows the session exists locally; it does not prove the
caller is that session.

### Bounded company Developer proposal and D6 completion evidence

`task delegate` can request a bounded company-gw code proposal for a PM-assigned
low-risk task. It reads the current root, branch, source HEAD and Executor identity
from the registered D6 record; do not supply a SHA as an identity claim. The
company provider returns a diff proposal only. An already authorized Executor
must apply it and run the allowed checks in its registered worktree.

For `task delegate --provider codex`, llmtool resolves the explicitly configured
`codex-developer` profile (`type: codex-cli`) and records that profile's model. It never uses
`codex-pm` as a fallback; if `codex-developer` is missing, the Developer is recorded as
`DEVELOPER_UNAVAILABLE` with `DEVELOPER_PROFILE_UNAVAILABLE`. The Codex CLI provider accepts the
same four optional `reasoning_effort` values described above.

Company-gw and Codex text proposals receive a small conservative normalization
before the unchanged strict unified-diff and write-scope checks: one outer
Markdown diff fence can be removed only when all text outside it is whitespace;
a missing `diff --git` line can be restored
when the old/new paths match, bare blank hunk lines become context lines, and
hunk counts are recalculated from the body. The accepted Task result records
only fixed normalization tokens. Mode changes, renames, binary patches,
multiple fences, mixed prose, and out-of-scope paths are not normalized into
acceptance. Rejected company-gw and Codex text proposals, plus collected direct
Codex diffs that fail output validation, are stored only in the owner-private
task state directory as a redacted, 64 KiB-bounded diagnostic file; the Task
record keeps its relative reference and SHA-256, never the rejected text. The
CLI does not print the capture or include it in evidence packets. Output that
cannot be safely collected is not captured.

For `task delegate --provider opencode`, llmtool accepts any well-formed `opencode vX.Y.Z`
CLI version and records it; the strict JSON event parsing still rejects incompatible output.
The model is `opencode/deepseek-v4.1-flash-free` unless an optional `opencode-developer`
profile sets `model`; only an `opencode/<name>-free` model is accepted, never a paid fallback.
The OpenCode agent step limit defaults to 12 and can be set with the profile's integer `provider_options.steps` value from 3 to 50.

For a direct OpenCode Developer task, the CLI now emits a completion event when the
bounded OpenCode process exits successfully. It rechecks the registered D6 worktree,
binds the actual uncommitted diff to the Task and Run, and runs only bounded tests
whose test files are inside the recorded task scope. If those gates pass, llmtool
sends a screened evidence summary in a new ephemeral call to the same persisted PM
profile. This does not resume a native PM session. The packet identifies the result
as an uncommitted worktree diff (`result_head: null`); independent review, PR and CI
remain pending. A missing test scope, changed worktree or unavailable PM is retained
as a waiting state. Ambiguous PM outcomes are not retried.
Nonzero, rejected or ambiguous Developer outcomes remain `WAITING_EXECUTOR`; they do not
trigger an automatic PM failure callback.

For final committed-result, Reviewer, PR and exact-head CI evidence, an operator
still explicitly runs
`llmtool task complete TASK_ID --executor-task D6_TASK_ID --reviewer-task D6_REVIEW_TASK_ID --pr PR_NUMBER`.
To prepare the formal Reviewer handoff, run
`llmtool task review-prompt TASK_ID --executor-task D6_TASK_ID --reviewer-task REVIEWER_D6_TASK_ID --pr PR_NUMBER`.
It prints the prompt only when the recorded Task result, both live D6 worktrees
and the public PR head all match; the prompt includes the exact verdict and
Task binding lines for that HEAD.
This manually invoked command checks the real D6 registration, current Git result,
the persisted proposal against the applied diff, bounded tests, public exact-head
PR/check data, and a formal review marker plus a binding line containing the
exact Task ID, Run ID, contract digest and result HEAD. The review actor must
match the registered D6 reviewer login, and the reviewer D6 agent/session and
native session evidence must be distinct from the Executor. The public review
and check APIs are read-only; later review dispositions and newer check runs
override older approvals or successes. Missing gates are retained as WAITING_EXECUTOR, WAITING_REVIEW or
WAITING_CI. Only a complete evidence packet is sent to the original PM profile;
the PM response is persisted as a recommendation and grants no execution or Git
permission. If the original PM is unavailable, complete evidence remains in
WAITING_PM. Ambiguous provider outcomes are not retried automatically.
A Task recorded with `task record-pm` has no PM provider profile: completion never
resolves or calls one. Complete evidence is stored as PM_EVIDENCE_READY, and
`task show` adds `active_pm_handoff` with the same bounded packet and instructions a
provider PM would receive, for the active PM runtime to read. All evidence gates
are unchanged.

Reaching PM_EVIDENCE_READY also stores one completion event in the Task's existing
event list. `llmtool task inbox --pm-session SESSION [--wait] [--timeout N]` prints, as
JSON, every unacknowledged event of Tasks recorded by that PM session, with the
handoff. With `--wait` it blocks (polling the store; no daemon) until an event exists or
the timeout (default 540 s, at most 3600 s) passes, then returns an empty list. A PM
runs it as a background command and reads events when it returns.
`llmtool task ack EVENT_ID --pm-session SESSION` marks an event received; repeating it
changes nothing. Events survive PM absence and restart: unacknowledged events are
returned on every read, acknowledged events never again. The event ID is derived from
the Task, Run and evidence digest, so re-verifying the same evidence never creates a
second event, and a READY Task whose event write was interrupted gets its event on
the next inbox read. Events and acknowledgements dispatch nothing; any later dispatch
still goes through its own one-shot claim.
A Task keeps at most 32 events. When the list is full, the oldest acknowledged event is
compacted into a tombstone that keeps its ID and stream position, so it is still never
recreated, and acknowledging it again returns the same success. Tombstones are never
dropped: once a Task holds 256, it records no further events and the inbox lists the
result under `blocked` with `COMPLETION_EVENT_HISTORY_LIMIT` and action
`START_NEW_TASK_OR_RUN`. If all 32 are unacknowledged, the inbox reports `status:
ATTENTION` and a `blocked` entry with `COMPLETION_EVENT_LIMIT` and action
`ACK_DELIVERED_EVENTS_THEN_READ_INBOX` (`--wait` returns at once); acknowledging the
delivered events and reading again records the new result. Other failures to record an
event are listed under `blocked` with action `RETRY_INBOX`.

Developer assignment inbox (#367 slice 1). `llmtool task assign --worker WORKER --d6 D6
--pm-session SESSION [--common-dir GIT_COMMON_DIR] [--task-id ID] [--owner-ref URL]
[--expires-in N]` re-runs the worker-dispatch gate, reads the D6 registration (it must be
ACTIVE; `--common-dir` defaults to the current repository's) and writes one ASSIGNED
event bound to that D6, its root and its HEAD (`source_sha`), expiring after N seconds
(1-900, default 600). A refusal prints `{"code": ...}`, exits 1 and writes nothing. A
second live assignment for the same D6 (any worker: an unexpired ASSIGNED, or a CLAIMED
at the same HEAD) is refused as `ASSIGNMENT_DUPLICATE`, so one D6 has one writer. The
Developer's resident session runs `llmtool task assignments --worker WORKER [--wait]
[--timeout N]` (ids only; bounded wait, N at most 600) and then `llmtool task claim
ASSIGNMENT_ID --worker WORKER --d6 D6 --session SESSION_UUID`. Before the one-shot
ASSIGNED -> CLAIMED write the claim re-runs the dispatch gate (the result is appended to
the assignment's audit; a PAUSED or unmatched OWNER_ASSIGNMENT_ONLY worker is refused with
its gate code), checks the worker (`ASSIGNMENT_WORKER_MISMATCH`), the D6
(`ASSIGNMENT_D6_MISMATCH`; no longer ACTIVE: `ASSIGNMENT_D6_NOT_ACTIVE`; HEAD moved:
`ASSIGNMENT_SOURCE_STALE`), the expiry (`ASSIGNMENT_EXPIRED`, and the assignment becomes
EXPIRED) and that this session's Claude Code transcript exists under the claimer's own
`CLAUDE_CONFIG_DIR` for cwd equal to the D6 root, as plain owned files with no symlinks
(`ASSIGNMENT_SESSION_NOT_FOUND`). A repeated claim by the same session returns the same
receipt; any other claim is refused with `ASSIGNMENT_ALREADY_CLAIMED`. The receipt holds
the session id, the claim time and `account_dir_label` (a digest of the claimer's
configuration directory path). It is accountability and fail-closed bookkeeping, not
authentication (every account runs as the same OS user), and never test, review or
completion evidence. The assigning PM's `task inbox` lists expired, unclaimed
assignments under `unclaimed_assignments` (state UNCLAIMED, `status: ATTENTION`; `--wait`
returns when one newly appears) and claimed ones with their receipt under
`claimed_assignments`. All assignments live in one locked (bounded wait, then
`ASSIGNMENTS_BUSY`), atomically replaced `.developer-assignments.json` in the TaskStore
root, so accounts that share the store share the inbox. At most 128 are kept; the oldest
finished record is compacted first and live assignments are never dropped.

The direct OpenCode worktree-result-to-original-PM handoff is automatic after the
verified process exit and bounded test gate. Later independent review, PR and CI
collection still use the manual `task complete` command; the full user-to-PM-to-review
workflow is not yet automatic. Registry and API facts are fail-closed evidence
sources, not protection against malicious same-user tampering. Company proposal and
PM-return live provider journeys still require their separately authorized runtime
acceptance.

**Formal Claude Reviewer verdict for `task complete` (Issue #352).** The repository is
private, so `task complete` reads PR, review, comment and check data through the signed-in
`gh` CLI (`gh api --method GET` on fixed repository paths, 8s and 1 MiB per read). llmtool
never reads the credential, and `GH_TOKEN`, `GITHUB_TOKEN`, `GH_HOST` and proxy variables
are not forwarded. Every agent publishes as the same GitHub account and GitHub refuses
APPROVE from a PR's own author, so the formal Reviewer may publish its verdict as a PR
conversation comment instead of a PR review, in the one standard
[formal verdict comment](../../skills/ai-team-delivery/formal-verdict.md) format, posted by
the Reviewer D6 record's `github_login`. Only an unedited Claude PASS in exactly that form is
accepted; a Codex PASS is advisory and ignored. The latest Reviewer disposition wins: any
other verdict that names the result HEAD (including an edited or malformed one), or a later
PR review, supersedes an earlier acceptance. A PR
with 100 or more conversation comments fails closed. The D6 independence, native-session,
reviewer worktree HEAD and exact-head CI gates are unchanged. Create the separate read-only
Reviewer worktree at the result HEAD with the optional lifecycle bindings
`--github-login LOGIN --native-session-id SESSION_ID` (a Claude Reviewer can be started with
`claude --session-id SESSION_ID`). A PR review and a verdict comment with the same timestamp
have no reliable order and are not accepted. Limitation: with one OS user and one shared
GitHub account, nothing binds a comment (or a PR review) to the Reviewer Session, so any agent
using that account could post a bound acceptance. This gate enforces process discipline, not
adversarial independence; the remaining control is the distinct D6 Reviewer record (agent,
Session, native Session and worktree at the result HEAD), and, like the registry, it is not
protection against same-user tampering. A separate Reviewer GitHub identity would be needed
for a real authorship binding.

Codex can use the same registered D6 direct Developer lifecycle with
`task delegate TASK_ID --provider codex --mode direct ...`; the default
`--mode proposal` preserves its text-proposal route. Direct mode requires the
`codex-developer` `codex-cli` profile, uses its model and reasoning-effort setting,
and invokes Codex CLI's own `codex exec --json` agent loop with workspace-write
sandboxing, bounded output and a wall-clock limit. It records the CLI thread ID as
the Developer native session, checks the resulting diff against `--write-path`,
persists the typed execution outcome and sends a successful bounded result through
the existing original-PM callback. Codex's requested self-review and test report
are used during the invocation but are not retained in the durable Developer record;
llmtool independently rechecks the D6 diff and runs its
bounded test gate before the PM callback. BLOCKED, malformed or ambiguous results
are retained without automatic retry.
The prompt carries the PM-selected Developer goal separately from the digest-bound PM intake contract and states that code changes must stay within the allowed write paths.
For a Codex `BLOCKED` result, the durable Developer record and `task show` include
only its sensitive-screened `blocked_reason` (up to 2 KiB); DONE and ambiguous outcomes retain no such explanation.

`task delegate ... --provider codex --mode direct --background` and
`task delegate ... --provider opencode --background` detach the direct Developer
after the assignment is claimed and its provider process is owned. The command returns a single
JSON receipt containing `task_id`, `state` (`DEVELOPER_DISPATCHING`) and the
provider child PID; the child continues through the same result persistence and
original-PM callback path as foreground delegation. Pre-launch refusals remain
synchronous. Without `--background`, delegation remains foreground.

The workspace-write sandbox controls writes, not reads: `allowed_read_paths` is
prompt scope only, and Codex may read other files available to its process.

How to run direct mode, and what each refusal means:

- Run it with llmtool's virtualenv Python (`tools/llmtool/.venv/bin/python -B -m
  llmtool ...`); without PyYAML the `codex-developer` profile cannot be read and the
  refusal says so.
- Run it with the current directory at the registered D6 root of `--executor-task`,
  on its registered branch and HEAD with a clean tree. Otherwise the refusal names the
  failed binding code (for example `D6_SOURCE_BINDING_MISMATCH`).
- With `--selection-source PM`, every `--acceptance` must repeat the acceptance that
  `task record-pm` recorded, exactly and in the same order.
- Direct mode takes no `--context-stdin`. Codex reads only the Task Contract goal,
  `--acceptance` and the allowed paths, so put any background the Developer needs
  in the PM's recorded goal or acceptance.

Before launch, llmtool runs the ENV-2 bootstrap for the D6 root. Bootstrap rewrites
the registry PASS marker, so llmtool rereads the D6 binding once afterwards: only
that marker may change, and it must be a PASS for the same root and HEAD
(`CODEX_ENV2_MARKER_INVALID` or `CODEX_D6_BINDING_CHANGED` otherwise, before Codex
starts). Codex runs its shell commands with no inherited environment and an explicit
`PATH`: the worktree's `tools/llmtool/.venv/bin` first, then fixed system
directories. An unexpected failure stays fail-closed with no automatic resend, and
is recorded with a stable code that names the binding stop or exception class
(`CODEX_ADAPTER_<NAME>`, never message or prompt text).

`git diff` / `cat` 是使用者在 shell 明確執行的上游程式；llmtool 只讀 pipe。
每次只接受一種來源：一個 positional argument、`--file` 或非空 stdin。
同時提供 positional 與 --file，或明確輸入加上 POSIX pipe/socket 當下已排入的非空資料，會在設定／provider 存取前報錯。
明確輸入遇到 TTY、closed/None stdin、regular-file stdin、/dev/null、空 pipe 或 EOF 不衝突；
`while read q; do llmtool ask "$q"; done < qs` 與 regular-file `--file` shell loops 可正常使用。
macOS/Linux 的 POSIX pipe/socket 使用 FIONREAD 探測，不讀取、不輪詢、不建立背景 thread；
不等待 writer 稍後產生資料或結束，也不探測已被其他程式碼緩衝的 bytes。
無法安全探測的 stream／非 POSIX 平台保留明確輸入行為，不因 probe 不可用而失敗。
stdin-only 模式先檢查 config/readiness，再讀至 EOF（有 16 MiB 上限）；實際測試含真實 pipe/socket/fd 與 shell loops。
互動終端不主動提示輸入；要用 stdin 可貼入 pipe 或重新導向。
輸入需為非空 UTF-8 文字，拒絕 NUL／二進位控制字元、非法 UTF-8、目錄與 FIFO 檔案。
接受一般 log 的 tab、換行、form feed 與 ANSI escape；大小上限 16 MiB。
`--file` 只讀你指定的檔案（可跟隨明確指定的 symlink），不遞迴、不自動補上下文。

| 命令 | Prompt 要求 |
|---|---|
| `ask` | Final answer only，明確區分證據與假設 |
| `analyze-log` | `ROOT_CAUSE`、`EVIDENCE`、`DISTRACTORS`、`UNKNOWN_OR_UNPROVEN`、`NEXT_COMMANDS`；最多 3 個唯讀建議指令 |
| `review-diff` | 只列 diff 支持的 findings：severity、file/snippet、failure/security mode、minimal fix |
| `context-pack` | A4 validated packet：`FACTS`、`EVIDENCE`、`INFERENCES`、`HYPOTHESES`、`UNKNOWNS`、`VERIFY_NEXT`；`--json` 輸出 JSON |

ask/review-diff 不驗證模型推論；analyze-log（包括 ask --skill analyze-log）另有有限的 deterministic NEXT_COMMANDS guard。
最多顯示三個實際 NEXT_COMMANDS 建議；接受普通／numbered bullets、inline backticks 加括號說明、
fenced commands、`NEXT_COMMANDS: (none)` 與結尾說明文字。大寫 `<SERVICE_NAME>` 等 placeholders
只在分類時視為資料名稱，不替換成真實值。輸出上限 1 MiB、每個 command 8 KiB。
NEXT_COMMANDS 的已知 MUTATING／DESTRUCTIVE、credential/private-data display、connection/auth overrides
或不支援的 shell interpreter／expansions／控制字元建議，會以可見 suppression marker 取代；
stderr 只記錄數量，不回顯被抑制的命令。全部建議被抑制時顯示 `(none)`，不產生替代命令。
ROOT_CAUSE／EVIDENCE／DISTRACTORS／UNKNOWN_OR_UNPROVEN 保留；可用的分析仍 rc=0，不 retry、不換模型。
A6 SENSITIVE_READ 表示未來執行需 approval，並非此文字建議的執行授權；systemctl status、kubectl describe、
journalctl 等正常診斷不因 SENSITIVE_READ 自動隱藏。UNKNOWN 仍是未證明安全、未批准的文字。
analyze-log **從不執行 NEXT_COMMANDS**。窄 guard 偵測 literal Secret/raw API／env／config display 與
secret-store reads，不解析完整 shell、aliases、動態 expansions 或證明模型推論安全。
Credential/private-data sources 包括 `/etc/shadow`、`/etc/gshadow`、`.pgpass`、
`/proc/<pid>/environ`／`/proc/self/environ`，以及不帶參數的 shell 變數 dump `set`；內容顯示建議會被抑制。
只對 command text 使用嚴格 invisible/control 規則；分析文字仍走既有 human terminal escaping boundary。
chat 的 explicit skill 回覆仍未套用此 CLI guard。第二輪隔離公司 gateway smoke 見
[Post-A9 validation](../../reports/llmtool_post_a9_hardening_validation_20261001.md#second-round-independent-review-follow-up--2026-10-02)。
context-pack 從 A4 起驗證 structured JSON schema，再輸出 human text 或 JSON；不驗證 claim 真實性。

Live smoke test 曾發現 `analyze-log` 建議 `cat /etc/company/app/config.yaml`；該命令沒有
執行。**READ-ONLY != SAFE-TO-DISPLAY**：唯讀命令仍可能輸出 credentials 或 private
configuration。這個案例已列入 Roadmap A3 skills 安全要求、A6 command safety validator
及 A8 regression；目前有 A6 deterministic validator 及 analyze-log 窄 post-check，但從不執行建議命令。

## C1 — Offline captured-test excerpts

**A-roadmap ownership:** this already merged, opt-in read-only utility is
A11 diagnostics support / A11c evidence preparation; `C1` is retained
here only for links to historical tests and PRs. No company/external
LLM export is authorized until the separately reviewed output boundary
and actual provider-data policy are satisfied.

Inspect one explicitly selected raw Python test-output capture, using the exit
code recorded by the caller when those tests ran:

```sh
llmtool test-excerpts --file captured-tests.log --exit-code 1 --json
llmtool test-excerpts --file captured-tests.log --exit-code 1 --json \
  --max-excerpt-bytes 1024 --max-excerpts 8
```

This opt-in command performs deterministic local parsing only. It does not run
tests, invoke a shell, inspect Git, scan the repository, read stdin, resolve user
configuration or credentials, call a provider, or write the capture. `--file`,
`--exit-code` and `--json` are required; provider options are unsupported.
Supply original captured stdout/stderr, never a model summary. The caller is
responsible for the capture's origin and truthful exit metadata: matching a
report's syntax does not authenticate its contents or test execution.

Version-1 JSON keeps evidence and test outcome separate:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `source` | Original SHA-256 and byte size; opaque `sha256:<digest>` reference to the caller-retained local capture; no input path echo |
| `test_exit_code` | Unchanged caller-supplied integer, -127 through 255; negative values represent captured signal termination |
| `detected_format` | `unittest`, `pytest`, `mixed`, or `unknown` |
| `status` | `COMPLETE` or `INCOMPLETE` for known failure-ID coverage, never a test PASS |
| `test_status` | `FAILED` for a nonzero exit or observed/reported failures; otherwise `UNKNOWN` or `NO_FAILURES_REPORTED`, never a claim that every test ran or passed |
| `failure_ids` / `failure_sources` | IDs in first-observed order, deduplicated without renaming; occurrence counts, kinds and the first raw header anchor |
| `excerpts` / `summary_sources` | Source references and original UTF-8 byte ranges, `[start_byte, end_byte)`; excerpts also include exact text, ID/kind and a truncation flag |
| `coverage` | Reported/observed failure counts, unittest run count (pytest: unknown/null), complete-ID/unknown/omission flags and fixed unknown-reason codes |
| `omitted` | Counts of omitted IDs/excerpts, truncated excerpts, unselected failure-block bytes and unselected source bytes |

`COMPLETE` requires one supported terminal report with consistent failure/error
counts and exit metadata. It does not mean complete traceback content: bounded
excerpts can be truncated or omitted while all known IDs remain covered. A
nonzero exit always remains `FAILED`, including when parsing is `INCOMPLETE`.
The command returns the supplied positive exit code (1–255), or 1 for a negative
captured exit, inconsistent metadata, incomplete coverage or rejected input.
It returns 0 only with a zero captured exit and a complete report of no failures.
Even a closed output pipe cannot turn a failed capture into command success.

Supported failure anchors are standard unittest `FAIL:`/`ERROR:` headers with
their separators and traceback blocks, plus `Ran ...` and `FAILED (...)`/`OK`
summaries; and pytest `FAILED file.py::test` / `ERROR file.py::test` short-summary
lines (including parametrized node IDs and collection-error file IDs), with the
short-summary banner and terminal outcome totals. Pytest excerpts contain the
justifying summary line, not guessed traceback locations. Chained unittest
tracebacks are retained within the block budget. Repeated IDs still count as
separate failure occurrences. Missing/partial summaries, malformed or ambiguous
IDs, mismatched totals, multiple/mixed runs, unterminated captures, unfamiliar
formats (including JUnit XML), and unsupported outcome structures produce
`INCOMPLETE` and explicit unknowns. Complex unittest subtest/class-setup counts,
ExceptionGroup layouts, custom reporters and unusual pytest node spellings may
remain incomplete; no root cause or assertion outcome is inferred.

Bounds: source ≤1 MiB and ≤32,768 LF lines; explicit path ≤4,096 UTF-8 bytes and
64 components; ID ≤512 bytes, at most 128 emitted unique IDs. Pytest header
content is limited to 8 KiB and 16 possible ` - ` delimiters. Each excerpt is
at most 2,048 original bytes (configurable down to 128), at most 32 excerpts
(configurable down to 1), with a fixed 16 KiB combined excerpt budget. Excerpt
ends align to UTF-8 boundaries; boilerplate/full-log dumping is avoided.
ID-limit overflow makes coverage incomplete; excerpt limits have explicit
omission/truncation counts. The original capture remains available locally by
the caller's file selection and hash; the utility neither copies nor freezes it.

File access requires POSIX no-follow directory/file descriptors. Every path
component must be physical; symlinks, directories, FIFOs, hardlinks, credential
locations, `.git`, oversized files, malformed UTF-8/binary controls and detected
file/directory identity or byte-count drift are rejected. On macOS use physical
paths such as `/private/tmp`, rather than the `/tmp` or `/var` symlink aliases.
ANSI escapes, bidi/invisible format controls and lone carriage returns are
rejected; ordinary Unicode, tabs, LF and CRLF are supported. Capture uncolored
output upstream (for example pytest's `--color=no`); this utility never executes
that upstream command. JSON escapes Unicode/control spelling safely on stdout;
stderr uses fixed categories without input paths, content or exception details.

Recognizable credential assignments, authorization values, key/token patterns
and credential-bearing URLs anywhere in the capture cause rejection before
any excerpt is printed. The same check runs again on every emitted failure ID
and final excerpt text, after byte, UTF-8 and aggregate truncation, both as
is and with non-ASCII characters backslash-escaped as in the ASCII JSON
output, because a cut or escape can create a match the full source lacked.
Any match rejects the whole result with a fixed, content-free error instead
of partial output. JSON's `\t`/`\n`/`\r` spellings are not modelled. This
conservative indicator check can reject benign fixtures and does not discover
every secret. Excerpts may contain sensitive
test data or traceback paths and remain **local evidence**. C1 authorizes no
transfer to company or external models: a separately reviewed permission and
redaction gate is required before any future handoff. No automatic routing,
executor, approval, Git mutation, or measured savings is provided. Deterministic
excerpt selection is not a token/cost benchmark; company-only Phase 0 on real
48 KiB+ captures and any paired premium-token measurement are future, separately
authorized work described in [the C1 plan](NATIVE_SELF_DEV_MVP.md).

## OpenAI-compatible thinking 與共用輸出契約

`enable_thinking` 是 OpenAI-compatible 實作的**可選 provider-specific 行為**，
不是 generic generation 的共同能力。Generic template 的 `provider_options` 是空的。
依 Owner 2026-10-28 決策，公司 Qwen profile 預設
`provider_options.enable_thinking: true`。需要關閉 thinking 時可明確設為 `false`；
一般相容伺服器若不支援此擴充，可設為 `null` 或使用空的 `provider_options: {}`。
設定 true 或 false 時，request 會帶上對應的已驗證欄位，例如：

```json
"chat_template_kwargs": {"enable_thinking": true}
```

新 profile 的通用預設仍省略此擴充，不繼承公司 profile 的設定。此選項只接受 boolean 或 null；
true 會要求相應伺服器啟用 thinking，
但 reasoning 欄位仍不輸出，也可能更快用完 completion budget。
Core／CLI 不生成此 wire 欄位，未來 provider 不必實作它。

模型命令成功時 stdout **只有** normalized final assistant text 加一個換行。
`openai-compatible` 使用 `choices[0].message.content`；`openai` 只取 Responses API
assistant `output_text`/text item。兩者都不輸出 reasoning、raw response 或 tool payload。
預設成功時 stderr 為空；`--verbose` 額外印安全的 model、token usage 與 elapsed metadata。
Help/version/config show/path 是管理命令，stdout 分別是說明／版本／設定／路徑；
config init 的完成訊息在 stderr。

結果會完整驗證後才寫 stdout；任何 provider 錯誤不輸出部分答案。
Ask/analyze-log/review-diff、chat/history 與 context-pack human output 在 stdout 為互動 TTY 時會把 ESC、BEL、C1、
CR、其他 C0 控制字元及 Unicode bidi formatting controls 顯示為可見跳脫文字；一般 Unicode、
換行與 tab 保留。JSON 與非 TTY/piped stdout 保持原始機器資料，不因終端顯示安全而改寫。

| Exit code | 意義 |
|---|---|
| `0` | 成功 |
| `1` | 設定、輸入、HTTP、timeout、解析或輸出錯誤 |
| `2` | 無效 CLI 語法 |
| `130` | 使用者中斷 |
| `129` / `143` | active CLI provider invocation 收到 SIGHUP / SIGTERM，清理後結束 |

HTTP 非 2xx、API error body、malformed JSON、missing/empty final text、Responses 未完成、
`openai-compatible` 的 `finish_reason=length` 或非 `stop`、以及不支援的 tool output 都失敗。
錯誤只給分類／HTTP status，不印 raw response、輸入、URL credentials 或 exception body。
不自動 retry、不跟隨 HTTP redirect，不讀環境 proxy 設定，避免意外轉送內網資料。

## Ask diagnostics — ephemeral by default

Only `ask` has the instrumented lifecycle; A11a.2 is merged. Without CLI or
trusted user opt-in, no diagnostics store/trace file is created and `LLMTOOL_HOME`
is ignored. Other commands,
including A9 orchestration and A10 retrieval, retain their output/call contracts.

```sh
llmtool ask "Explain this supplied evidence" --verbose
llmtool ask "Explain this supplied evidence" --debug
```

Normal success keeps final-text stdout and quiet stderr. Expected ask failures
keep their original safe first line and append:

```text
llmtool: code=PROVIDER_TIMEOUT run_id=<32 lowercase hex characters> trace=not-saved
```

`--verbose` keeps validated provider model/token/elapsed keys in their previous
order, then adds run_id, provider_type (when available), and
`diagnostics_persisted=true|false`. `--debug` implies verbose and adds safe event
summaries: ID, type, component, severity, and available duration. It never displays
prompt/response, reasoning, raw stderr/body, environment, host paths, or Python
tracebacks. Invalid/credential-looking metadata is omitted; it cannot fail the
primary request. No tokens are estimated.
An event-only schema or construction failure preserves already validated legacy
verbose metadata; the rejected event is counted without suppressing that display.

A successful ask emits run.start → provider.request → provider.response →
run.end(success). Expected errors emit error then run.end(error), except incomplete
model output ends as incomplete. End severity is INFO for success/output_closed,
ERROR for error, WARNING for interrupted/incomplete. Exit status remains 0/1/2/130;
BrokenPipe keeps 0. Argparse failures do not create a run.

Safely parsed OpenAI-compatible failures can also emit provider.response before
error when the response supplies a complete, valid usage triplet. The typed
`LLMToolError.provider_metadata` property holds a detached, read-only mapping of
`prompt_tokens`, `completion_tokens`, and `total_tokens`, or `None` when unavailable.
Each count must be an integer (not a boolean) between 0 and 10^12, and total must
equal prompt plus completion. Invalid or partial failure usage is omitted without
changing the original error message, code, exit status, or retryability. Invalid
JSON, oversized responses, and transport failures have no usage metadata. This
numeric metadata follows the existing verbose/debug and explicit persistence
settings; it never includes response text and never makes a failed request succeed.

Events are bounded to 16 KiB each, 32 per run and 64 KiB total. Construction or
adapter failure drops observation, counts it, and attempts at most one fixed
DIAGNOSTICS_EVENT_DROPPED warning before finalization. Warning failure never
recurses; display failure is accounted without appending past run.end. Normal
success still prints no diagnostics warning. An exceptional ID generator failure
uses `run_id=unavailable` rather than inventing an insecure identifier.

Codes come from input/config stages and numeric/typed provider failure sites,
never message substring matching. All recovery flags are metadata only;
`fallback_allowed=false`. Every provider execution attempt remains one complete()
call, with no retry or fallback. A11a.6 preserves fixed unexpected-error text
and safe debug event summaries: Python stack/traceback paths, locals and exception
repr are not collected or displayed.
JSON is the coded-diagnostics transfer format; coded pickle/deepcopy remain
unsupported. Diagnostics files are saved only by the explicit metadata opt-in
described below; merged A11a.5 provides the offline `trace` reader.

See [A11a.2 validation](../../reports/llmtool_a11a2_ephemeral_ask_diagnostics_validation_20261002.md).

## Diagnostics settings and store — A11a.3 and A11a.4 merged

Only trusted user `~/.config/llmtool/config.yaml` may contain this optional mapping:

```yaml
diagnostics:
  persist_metadata: false
  max_runs: 100
  max_total_bytes: 67108864
  max_age_days: 14
  max_run_bytes: 1048576
```

`persist_metadata` must be a boolean. Limits require integers: max_runs 1–1000,
max_total_bytes 1–512 MiB, max_age_days 1–365, max_run_bytes 64 KiB–8 MiB and
no greater than max_total_bytes. Unknown fields, wrong types and out-of-range
limits fail configuration before provider access. Project `.llmtool.yaml` cannot
set diagnostics. `config show` keeps its existing provider/generation output;
`configure` preserves a validated diagnostics mapping without expanding it.

Ask accepts mutually exclusive `--save-diagnostics` / `--no-save-diagnostics`.
Precedence: explicit CLI save/no-save > trusted user `persist_metadata` > false.
The unspecified argparse value is `None`; no generic environment variable enables
persistence. `--no-save-diagnostics` disables even trusted user opt-in. Both flags
are ask-only; conflicts and flags on other commands remain usage errors (exit 2)
before observation or store access.

```sh
llmtool ask "Explain this supplied evidence" --save-diagnostics
llmtool ask "Explain this supplied evidence" --save-diagnostics --debug
llmtool ask "Explain this supplied evidence" --no-save-diagnostics
```

Legacy skill and input-source validation run first. Opt-in then resolves settings
and starts the store before readiness, blocking input reads and provider work.
The final store collision-resolved ID is assigned to buffered run.start and all
subsequent events before the first append; events, summary, directory and displayed
run_id agree. Valid settings still allow early input/skill errors to be recorded:
settings resolution after such an error is best effort and never replaces the
original error. Unusable settings plus explicit save report one secondary
unavailable warning without opening the store. When no earlier error exists,
invalid diagnostics settings fail configuration before any store/provider access.
Only validated metadata events and the versioned summary are saved, never prompt,
response, raw provider stderr/body, credentials, reasoning or host paths.

`LLMTOOL_HOME` selects a location without enabling persistence. It must be an
absolute path without controls or parent traversal; shell expansion is not
performed. Defaults: macOS `~/Library/Application Support/llmtool`; Linux
absolute `$XDG_STATE_HOME/llmtool`, falling back to `~/.local/state/llmtool`.
Windows `%LOCALAPPDATA%\\llmtool` is reporting-only; the persistence backend
rejects unsupported platforms. Ask resolves/creates this home only after opt-in;
unsupported persistence warns once and continues the existing ask unsaved.

The standalone `diagnostics_store.DiagnosticsStore` API is opt-in and POSIX-only.
It accepts validated typed events and a typed `RunSummary`; construction/default-off
start perform no filesystem access. An enabled start securely anchors traversal,
creates directories 0700/files 0600, rejects symlink components, unsafe existing
permissions/ownership and hardlinked lock/data files. It does not chmod unsafe
existing stores. File operations use directory descriptors and no-follow flags.
Store/run flock locks protect cleanup, no-clobber run creation, active writers and
full max-run capacity reservations. Run ID collisions have three bounded attempts.

Completion fsyncs and atomically publishes `trace.jsonl` and `summary.json` and
syncs the run directory; this durable publication commits persistence success.
Reservation release follows as maintenance: a root-lock timeout or cleanup failure
does not report the committed trace as unsaved. A later enabled start removes a
stale reservation only after validating its completed summary and trace while
holding exclusive root/run locks, then charges actual bytes. Unsafe, unknown,
malformed or incomplete records retain conservative capacity accounting.
Readers must require the summary as the completion marker;
the two files are published individually. Ask interrupts finalize with status
`interrupted`, including handled CLI-provider TERM/HUP cancellation (exits 143/129).
Healthy persistence publishes the completed interrupted summary before the signal
exit, freeing the run for normal capacity eviction immediately. Diagnostics failures
remain secondary and do not change those exit codes. HTTP TERM/HUP retains the default
OS disposition and can leave an incomplete run. Broken output pipes finalize with
`output_closed`. A crash or store
failure can leave a partial trace or a trace without summary. Cleanup only occurs
on enabled start: age, count
and byte limits evict validated eligible runs, active locks prevent deletion, and
abandoned runs have a one-hour grace. Equal completion times use run_id as a
deterministic eviction tie-break. Unknown data remains and consumes capacity;
malformed reservations disable new persistence. Failed summary temp files are
retained conservatively when their ownership/schema cannot be proved.

The API returns unavailable/false plus one fixed bounded `StoreWarning` instead of
raw exception/path data. Specific store codes distinguish unsafe state, capacity,
collision and write failures. A11a.4 connects these results to ask: successful
stdout and exits 0/1/2/130 retain their contracts; each existing provider execution
still makes exactly one request. Creation/append/publication/close errors preserve
the task result and emit at most one fixed bounded store warning. On task errors,
the original safe first line and code/run_id line precede that warning. Failure
metadata reports `trace=saved` only after successful completion, `unavailable`
after requested persistence fails, and `not-saved` when persistence is off.
Verbose/debug report the actual completion result in `diagnostics_persisted`.
The integration attempts every remaining descriptor release even when one close
fails; it never retries the provider or store publication. Unknown partial state
is preserved conservatively. Offline runs/trace merged in A11a.5; merged A11a.6 adds offline doctor
without enabling persistence.
See [A11a.3 validation](../../reports/llmtool_a11a3_secure_diagnostics_store_validation_20261004.md).
See [A11a.4 validation](../../reports/llmtool_a11a4_persisted_ask_validation_20261003.md)
for Cloud/offline evidence, R1 closure, external source review and reviewed-head CI.
PR #19 merged as `2bade6a`; post-merge main CI `37167325001` passed Python 3.9/3.14.
Historical exact Git objects were published through existing GitHub API access;
direct Git RPC push authentication failed at that checkpoint. No new live inference
is claimed by local mock HTTP or real Linux filesystem validation.

## Offline diagnostics readers — A11a.5 merged

```sh
llmtool runs
llmtool trace 0123456789abcdef0123456789abcdef
llmtool trace 0123456789abcdef0123456789abcdef --errors
llmtool trace 0123456789abcdef0123456789abcdef --errors --json
```

These commands read saved metadata from the same `LLMTOOL_HOME` resolver without
reading user/project config, credentials or provider session/auth files, or
calling a provider. They never create storage, clean old runs, repair permissions
or alter records. POSIX macOS/Linux support uses anchored no-follow read-only
descriptors, effective-user/private ownership checks and shared existing locks
(root before run). Root acquisition uses nonblocking attempts with a bounded
two-second retry; contention timeout reports `STORE_UNAVAILABLE`. Run acquisition
is immediate and never waits/retries while holding root. Other backends fail safely. Missing lock files,
symlinks, unsafe modes/owners, hardlinks and nonregular artifacts are rejected.

`runs` renders up to 100 newest validated summaries, ordered by started_at then
run_id descending. Rows label summary status as `recorded_status`; the listing reports
that trace evidence is unverified. It checks summary metadata and artifact
security without reading trace bodies. A recorded success is not evidence of
a completed run; use `trace <run_id>` to verify completion. A completed-looking
active run is incomplete. The run lock is checked before mutable artifacts;
publication temporary files and transient hardlinks in a busy run are not scanned.
No-summary rows have unknown times and sort last. Damaged and unrelated entries
are summarized by generic counts; their names/content never appear. The scan
includes unrelated entries and fails above 1,000 entries; each run permits at
most 16 artifact names. Summary reads are bounded to 4 KiB each and 4 MiB total.
An absent store returns exit 0 with `No saved diagnostics runs.` and creates nothing.

`trace` accepts only 32 lowercase hexadecimal characters. Completed status
requires a valid summary and matching trace IDs, ordered event sequence, counts,
bytes, start/end times, mode, command, status and duration. An active run returns
incomplete with no events; a stable partial or published-without-summary trace
remains incomplete. Only complete valid JSONL lines are displayed; an unfinished
bounded final line is omitted. Completed corruption fails with empty stdout.
`--errors` selects structured error/warning event types after the entire trace
validates; severity alone does not select run.end or unknown types.

Trace file reads are bounded to 8 MiB, 16 KiB per line and 32 events; JSON depth
is at most 3 with 64 values per document. Duplicate keys, nonfinite numbers,
invalid UTF-8, dangerous controls, recognizable credential patterns and invalid
required fields fail safely. Version 1 extensions validate within bounds and are
ignored; unsupported versions return `FORMAT_UNSUPPORTED`. Unknown v1 event
types are generic metadata with type/component `unknown` and empty data.

Trace JSON is the explicit `llmtool.diagnostics.trace` schema, version 1, with
`run_id`, `state`, projected `summary`, projected `events`, `filtered` and
`omitted_partial_line`. Summary fields are run_id, command, mode, status,
started_at, ended_at, duration_ms, provider_type, final_error_code, event_count,
dropped_event_count and trace_bytes. Event fields are event_id, timestamp, type,
component, severity, span_id, parent_span_id and allowlisted data. Reader output
omits stored free-text messages/model/profile strings and arbitrary extensions;
closed codes/flags/numeric cause metadata identify failure layers. It never dumps
raw JSONL, prompts/responses, exception text, credentials or host paths.
Human output uses the same projection. Output is fully bounded to 256 KiB before
stdout; oversized output fails with no partial JSON. Reader errors use fixed
codes/messages on stderr and exit 1 (missing run: `NOT_FOUND`; invalid ID:
`ID_INVALID`; unexpected I/O failure: `STORE_UNAVAILABLE`; invalid metadata:
`FORMAT_INVALID`). Usage 2, interrupt 130 and broken pipe 0 remain unchanged.

See [A11a.5 validation](../../reports/llmtool_a11a5_runs_trace_validation_20261004.md)
for exact bounds and historical offline/security/race evidence.
[PR #22](https://github.com/davidcatcat/davidcattoolsproject/pull/22) merged as
`da027f0d728e3b3ff605a6d404c49c8beb08ec28`; post-merge main
[CI 37193806074](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37193806074)
passed Python 3.9/3.14, each 824 tests. Earlier pending/review statements in that
report describe historical checkpoints and remain unchanged.

## Offline doctor — A11a.6 merged

```sh
llmtool doctor
```

Doctor performs offline inspection only. It prints platform persistence support,
trusted user persistence/retention settings, the selected provider type and
configuration readiness, CLI executable availability, credential configuration
presence, and existing diagnostics store structure/permission counts. Normal
config layering and provider selection still apply; project diagnostics settings
remain forbidden. `LLMTOOL_HOME` selects a location and never enables persistence.

HTTP readiness uses existing pure configuration checks. CLI availability uses
`which` and the same absolute-path requirement as the provider; doctor never runs
that executable, checks its version, logs in or probes authentication. Credential
environment presence checks keys only, even for an empty value. Literal credentials
are reported only as `literal-configured`; no credential values are resolved or
printed. Output excludes profile/model names, URLs, paths and environment names.
Presence/readiness proves neither authentication nor inference.
Doctor fails closed with `CONFIG_INVALID` if any configured credential environment
binding overlaps a configuration/location/runtime variable it consumes (including
`LLMTOOL_MODEL`, `LLMTOOL_PROVIDER`, `LLMTOOL_HOME`, `HOME` or `PATH`). This
inspection-only restriction does not change normal ask/config behavior. Windows
environment names are compared case-insensitively for both reserved bindings and
key-only credential presence; POSIX comparisons remain case-sensitive. It is
checked before configuration overrides, diagnostics location or executable lookup
once trusted bindings are known. Locating the trusted user config still uses the
existing HOME/default-path bootstrap before those bindings can be read; doctor
does not change config-path precedence or discover alternate credential files.

Existing stores use anchored no-follow read-only directory/lock opens and bounded
shared locks, owner/private-mode/single-link/identity checks. Doctor inspects names
and file sizes without reading trace, summary, reservation or lock contents.
Active runs skip mutable artifacts and are counted as active; unknown entry names
are counted without following or displaying them. Metadata completion, task health
and write access remain explicitly unverified. Doctor creates/repairs/cleans/writes
nothing, including when persistence is configured. Reader bounds remain 128 home
components, 1000 run entries and 16 files/run; existing file-size bounds apply.

A completed factual inspection exits 0, including absent store, unsupported
persistence platform, missing executable or unconfigured readiness. These labels
are observations, not a successful backend validation. Invalid config exits 1 with
fixed `CONFIG_INVALID`; unsafe/unavailable store, race and bound failures use the
existing reader codes. Output is buffered until inspection succeeds, so failures
produce empty stdout and safe fixed stderr. Usage 2, interrupt 130 and broken pipe
0 remain unchanged. The command accepts no provider override, JSON, online/probe
or save flag. Existing `ask`, `runs` and `trace` contracts remain unchanged.

See [A11a.6 validation](../../reports/llmtool_a11a6_offline_doctor_validation_20261004.md)
for the named spec failure matrix and historical offline fault-injection evidence.
A11a.6 offline doctor/failure matrix and M1/M3 persistence hardening merged via PR #24 (`6b7809a`). A11a.7 final acceptance merged via PR #25 (`e3869828`), and post-acceptance B1–B7 fixes merged via PR #26 (`2761ce10`); post-merge CI `37228032137` passed Python 3.9/3.14. Overall **A11a DONE**. Historical validation/review reports retain their original checkpoint wording.

## Troubleshooting 與已知限制

Post-acceptance B1–B7 hardening is merged. Eviction now uses a durable intent plus quarantine/recovery flow, and committed diagnostics remain saved even if later cleanup/close reports a secondary warning. Remaining non-blocking observations are explicit: buffered broken-pipe handling for older non-A11a commands such as `analyze-log` is still legacy behavior; an extreme SIGKILL can conservatively leave an empty `.evicted-*` directory; and a kill before eviction-journal publication can leave an `.eviction-init-*` staging artifact. These do not consume a run slot or authorize automatic deletion of unknown data, and remain candidates for later hardening rather than A11a blockers.

Current diagnostics limits are retained from the external reviews: M2 late partial
trace/summary remnants can retain capacity; M4 full completed-trace validation
runs under the root lock; M5 reader/store scan limits differ. Doctor P1–P4 behavior
is unchanged: damaged artifacts can fail the entire inspection with empty stdout;
structural presence proves no write capacity or completed metadata health; an
empty configured credential environment value still counts as present; broad
inspection errors use fixed safe classifications. P5 stale status wording is
updated here; the original finding/report remains historical, with no doctor
behavior remediation claimed.

N1 unpublished `.run-init-*` staging directories are preserved, consume byte/scan
capacity and have no doctor staging count or automatic cleanup. Legacy malformed
reservations remain fail-closed. Publication requires native no-replace directory
rename support; unavailable/cross-filesystem rejection leaves the primary ask
result independent. Published abandoned runs retain the one-hour grace. SIGKILL
coverage does not prove power-loss durability or arbitrary same-UID namespace
isolation. `runs` keeps recorded-status/unverified evidence, null times and last
ordering for no-summary rows; it supports no `--json`. No automatic repair,
new cleanup policy, retry/fallback or A11b executor is added by final acceptance.
See [external re-review](../../reports/llmtool_a11a6_persistence_hardening_rereview_20261004.md)
and [A11a.7 final validation](../../reports/llmtool_a11a7_final_validation_20261004.md).

- `PyYAML is required`：確認啟用安裝 llmtool 的 virtualenv。
- `provider ... is not configured`：依 active provider 設定 model、相容 provider 的 endpoint，
  以及明確的 API key 或 `api_key_env`；`config init` 本身不完成部署設定。
- `cannot connect`：檢查設定中的 provider destination 和網路。OpenAI 固定使用官方 HTTPS
  origin；compatible provider 使用自訂 origin。HTTP transport 不採用 ambient proxy。
- `HTTP 401/403`：確認明確設定的 API key 或 `api_key_env`；OpenAI API billing 與 ChatGPT
  subscription 分開。
- `timed out`：以 `--timeout 180` 調整。這是 socket 操作 timeout，**不是整體 wall-clock deadline**。
- `finish_reason=length` / incomplete Responses：提高 `--max-tokens` 或縮小問題；截斷內容不會輸出。
- `malformed YAML`：檢查縮排、型別與重複 key；不會在錯誤訊息中回顯 YAML 原文。
- `only one ...`：不要同時 pipe 並給 `--file`／文字 argument。
- `config already exists`：保留現有設定，或明確以 `--force` 重建。
- `project ... forbidden or unsupported field`：將 endpoint/auth/selection 等設定移到 user config。
- `active provider is not defined`：先在 user config 建立該 profile；project 不能建立它。

沒有 tokenizer／context window 自動切分；所選 model/service 決定可接受的 token 數。
HTTP response 上限 16 MiB、config 上限 64 KiB。非串流模式會在記憶體保留完整輸入與答案。
A1/A2.1 信任相容 endpoint 將 reasoning 放在獨立欄位；不會修改／剝除 content 內的文字或標籤。
模型可能忽略 prompt 或產生錯誤建議，請 review 輸出；工具不會執行它。

## Safety boundary

`ask`、`analyze-log`、`review-diff`、A9/A10 與一般 chat 的文字 provider 不執行模型回傳的 shell/Git 指令；`claude-cli` 文字 transport 使用固定 argv、無 shell，並要求停用 CLI tools、session persistence 及自訂 hooks/plugins/MCP。PM runtime 與經授權的 direct Developer 是另外的執行路徑；它們有獨立的本機記錄與執行邊界，direct Developer 可在核准的 D6 寫入範圍內修改檔案。
明確的 config 操作及 A7 feedback capture/review 可寫入指定檔案；A9 orchestration 與 A10 retrieval 不寫檔。
不自動讀取 SSH、keychain 或任意 secret file；模型命令明確提供的文字、pipe、`--file`
內容會完整傳給 gateway，請自行移除不應傳送的秘密。離線 `test-excerpts` 只處理
明確指定的本機 capture，不傳給任何 provider。
一般 chat 預設將 user/assistant turns 保存至 `~/.local/state/llmtool/chat/history.json`；其目錄為 `0700`、檔案為 `0600`，內容經敏感資訊樣式篩查，最多保存 200 組 exchanges，序列化檔案上限為 256 KiB。`llmtool chat --no-history` 只停用一般 `ChatSession` 對這份 history 檔的載入與保存；它不會停用 PM runtime、Task store、task-status cache、測試記錄或外部 CLI 的獨立儲存。本機儲存依子系統分開管理。PM runtime 會在 `~/.local/state/llmtool/pm-runtime/` 保存 `<key>.history.json`、binding 與 runtime metadata；history 保留最近 200 筆 PM turn records，沒有一般 chat 的 256 KiB 檔案上限，也沒有對應的停用旗標。Task 操作會在 `~/.local/state/llmtool/tasks/` 保存 request、response、proposal、evidence、events、receipts 與相關狀態，並可能保存經遮罩的 rejected-output capture；`task status` 可更新相鄰的 `task-status-index.json` cache。Ask diagnostics 另受 `--save-diagnostics`、`--no-save-diagnostics` 與 trusted user 的 `diagnostics.persist_metadata` 控制，預設關閉，只保存其限定的 metadata。測試、High Debug、AGY、review artifact 與 D6 helper 路徑也會建立各自的記錄或暫存檔；部分 raw test/native log 在寫入前不做內容篩查。Config 與 feedback 由明確操作保存；benchmark adapter 本身只輸出結果，不自動保存 benchmark report。外部 CLI 的 session、cache 與 runtime state 依該路徑及 CLI 實作另行管理；llmtool 沒有全域「完全不落盤」開關。

## 開發與驗證

llmtool CI uses immutable action SHA pins, `persist-credentials: false`, read-only
permissions, a 15-minute timeout and clean-tree verification after offline tests.
`requirements-ci.txt` pins PyYAML 6.0.3 (the current validated runtime/test dependency);
it is an exact version pin, not a cryptographic hash lock. CI installs it first, then
uses `pip install --no-deps -e tools/llmtool`; package dependency compatibility is unchanged.

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

`check` 跑 compileall syntax check 與 unittest。測試使用暫存 HOME/project、mock
transport、fake Claude executable 和 `127.0.0.1` ephemeral port 的假 gateway；不使用真實
user config 或 credentials，也不連公司 gateway、OpenAI API 或真實 Claude CLI。OpenAI
provider tests 將 official origin 替換為 loopback mock HTTP server；Claude CLI tests 驗證
argv、stdin、timeout、output limit 與 sanitized errors。Live runtime validation 另行記錄，
不由 mock/unit test 推定。

```text
llmtool/cli.py                         CLI 與輸出
llmtool/chat.py                        conversation state and REPL; optional private local history
llmtool/chat_history.py                private, bounded local chat history (0700/0600, screened)
llmtool/chat_tui.py                    opt-in full-screen chat UI (`chat --tui`)
llmtool/context.py                     immutable packets, bounded parsing and rendering
llmtool/config.py                      YAML 合併、驗證、遮罩、init
llmtool/inputs.py                      明確輸入來源、文字驗證
llmtool/prompts.py                     common rules and skill composition
llmtool/skills.py                      typed declarative resource loader
llmtool/skills/*/SKILL.md               packaged runtime prompt resources
llmtool/feedback.py                    immutable feedback/proposal/regression data and offline evaluator
llmtool/feedback_files.py              explicit bounded reads and atomic case writes
llmtool/feedback_cli.py                offline capture/show/validate/review/regression adapter
llmtool/benchmark.py                   A7-derived quality, efficiency, runtime and safety measurement
llmtool/benchmark_cli.py               offline explicit files/suites and opt-in single live request
benchmarks/                           repository-local synthetic benchmark fixtures (not packaged)
feedback/                             repository-local curated regression fixtures (not packaged)
llmtool/providers/base.py              capability model 與 Provider.complete() / Reply contract
llmtool/providers/__init__.py          static provider registry、capability lookup 與 factory
llmtool/providers/openai_compatible.py HTTP transport 與 response 驗證
llmtool/providers/openai.py            固定官方 Responses API transport 與 normalization
llmtool/providers/claude_cli.py        local Claude CLI subprocess 與純文字 Reply normalization
llmtool/providers/codex_cli.py         isolated Codex CLI subprocess 與純文字 Reply normalization
tests/                                config/provider/CLI 測試
```

執行路徑為 task → prompt template → selected provider → `complete()` → final output。
Provider implementations 透過靜態 registry 宣告 type、capabilities、option/readiness
validation 與 `complete()`；A2.2 之後擴充 provider，A3 提供 declarative runtime Skill Framework。其餘
階段及 provider/role 分離方向見 [Roadmap](ROADMAP.md)。

## A3 — Runtime Skill Framework

A3 is DONE and merged to main in `c9159f2`; canonical validation and external review PASS. **Provider != Role != Agent != Skill.** A skill is
instruction/context data and grants no execution ability. Repository engineering
workflows in root `skills/*/SKILL.md` are separate and are never loaded by llmtool.

Runtime resources live at `tools/llmtool/llmtool/skills/<directory>/SKILL.md` and
are included as package data in installations. The fixed package-local root is
used regardless of cwd. There is no global search, plugin system, configurable
root, default selection, or saved skill configuration.

```yaml
---
name: analyze-log
version: 1
description: Analyze supplied logs and identify supported causes.
---
Instructions as Markdown text.
```

Frontmatter requires exactly `name`, `version`, and `description`. Names match
`[a-z0-9][a-z0-9-]{0,63}`; versions are positive YAML integers (not booleans,
strings, or semantic-version strings). Description and body must be nonempty
text without unsafe control characters. Unsupported fields, duplicate YAML keys,
aliases, unsafe YAML tags, duplicate declared names, symlink entries/files,
nonregular files, invalid UTF-8, and files over 64 KiB are rejected with sanitized
errors. Discovery reads immediate skill directories only and sorts by declared
name. Directory names follow the same safe naming rule; exact selection uses
the declared name. The installed resource tree is trusted and must not be
concurrently modified during loading.

```sh
llmtool skills
llmtool skills --json
llmtool skill show analyze-log
llmtool ask --skill analyze-log "supplied log evidence"
llmtool ask --skill review-diff --file supplied.diff
llmtool ask --provider PROFILE_NAME --skill test-triage --file bounded-test-failure.txt --verbose
llmtool ask --provider PROFILE_NAME --skill log-triage --file bounded-log.txt --verbose
llmtool context-pack --provider PROFILE_NAME --file bounded-evidence.txt --json --verbose
```

Listing/show are offline and bypass config, credentials, and providers. Text and
JSON listing are deterministic; show prints validated metadata JSON followed by
the body. Unknown names fail without echoing the supplied name. Selection does
not mutate config. Only `ask` accepts explicit `--skill`; existing `analyze-log`,
`review-diff`, and `context-pack` commands load their matching packaged resources.

Composition happens before provider dispatch: one system message contains common
llmtool rules, then task instructions, then (for explicit `ask --skill`) a labeled
skill name/version and its body. A separate user message preserves supplied text
exactly. All providers continue receiving the same list of role/content mappings;
provider clients contain no skill logic. The generic ask instruction remains in
code. The legacy task instructions and the development-time triage instructions are
versioned resources. `test-triage` and `log-triage` are provider-agnostic prompt
resources: they add no routing, parser, tools or authority. General evidence
normalization reuses the existing `context-pack` skill instead of creating a duplicate
`evidence-summary` skill.

Skills cannot define command/executable/hooks/pre_run/post_run/shell/Git/tools
fields. Loading uses strict SafeLoader-based text parsing, never imports skill
code or evaluates instructions. There is no shell/Git execution, model-directed
filesystem mutation, automatic tool use, or output execution added by A3. Existing
provider transports and explicit config writes retain their existing boundaries.
Model-returned command text remains only text; prompt instructions are not an
output validator. Current analyze-log CLI additionally uses the Post-A9 narrow
NEXT_COMMANDS guard described above; A3 resource loading itself stays declarative.
The packaged skills `analyze-log`, `review-diff`, `context-pack`,
`test-triage` and `log-triage` use only supplied evidence. The two triage skills
are intentionally weaker than a final reviewer: they do not decide root cause, fixes,
test correctness, security/QA PASS or merge readiness. Sensitive input must be bounded,
permitted and deterministically pre-redacted/discarded before any provider call.
Model-side redaction is best-effort only and must never be relied on. Returned test/file
identifiers must be checked deterministically against the supplied evidence before use
as locators. READ-ONLY != SAFE-TO-DISPLAY.

Historical A3 framework validation: 18 targeted A3 tests PASS; canonical syntax check
and 207 tests PASS (exact commands in the
[A3 validation report](../../reports/llmtool_a3_skill_framework_validation_20260930.md)).
Those historical counts predate `test-triage` and `log-triage`; do not cite them as
validation of the new dogfood resources. Current offline and Mac+VPN validation for
those additions is tracked separately in
[the dogfood skills handoff](../../reports/llmtool_company_dogfood_skills_validation_20261006.md).
A3 itself does not add roles, orchestration, retrieval, an A4 context schema, A5 Git
collection, or A6 validation.

## A3.5 — Interactive Chat Shell

`llmtool chat` starts an interactive process with local conversation history by
default. Provider != Role != Agent != Skill. A3.5 is DONE and merged to main
in `021456b`; canonical validation and external ChatGPT review PASS.

```sh
llmtool chat
llmtool chat --provider office-qwen --skill analyze-log
```

Chat accepts the same explicit provider/generation options as ask and optional
`--skill`. Initial config resolution uses normal user → project → environment → CLI
precedence. The session copies the resolved config once. Provider-specific startup
overrides stay attached to that initial profile; switching does not reapply them
or reload config. Missing deployment settings do not prevent offline session
controls; requests and provider switches validate readiness normally. Credentials
are resolved only when sending a user turn, not during slash commands or startup.

| Command | Behavior |
|---|---|
| `/help` | Show commands without a request |
| `/status` | Show profile name, provider type, skill, message count, and last-request status (below) |
| `/provider` | Show current profile and sorted configured profiles/types |
| `/provider <profile>` | Switch for subsequent requests; preserve history |
| `/skill` | Show selected skill and available runtime names |
| `/skill <name>` | Select a runtime skill for subsequent requests |
| `/skill none` | Clear the skill; preserve conversation |
| `/clear` | Clear in-memory and saved conversation; keep provider and skill |
| `/history` | Display user and assistant messages, excluding system context/metadata |
| `/exit`, `/quit` | Exit with status 0 |

By default, chat loads saved turns at startup and saves successful turns locally
in `~/.local/state/llmtool/chat/history.json`. The state directory is owned by
the current user with mode `0700`; the history file is owned by the current user
with mode `0600`. Saving keeps at most the newest 200 exchanges and trims older
exchanges as needed to keep the JSON file at or below 256 KiB. On save and load,
sensitive-looking content is screened; detected content is represented as
`[withheld]`. If the history file is unavailable or invalid, chat
continues without saved history and reports that history could not be loaded or
saved. `llmtool chat --no-history` disables loading and saving of the normal `ChatSession` history file while retaining that conversation in memory; it does not disable independent PM, Task, cache, test-log or native CLI storage. This option also applies with
`--tui`.

**Switching providers sends retained conversation history to the newly selected
provider on the next user turn.** The switch prints this notice. Use `/clear`
before the next message if that history should not be sent. Switching itself
makes no provider request and does not modify saved config. Unknown/incomplete
profiles and invalid skills leave selection and history intact.

`ChatSession` owns the in-memory config snapshot, active selection, successful
turns, and active flag. Each request builds one system message from common
llmtool rules plus the current optional labeled skill, followed by successful
user/assistant turns and the new user message. There is no repeated skill text
in historical turns. Changes to skills rebuild only system context; earlier user
text is unchanged. The conversation turn count counts individual user/assistant
messages: one completed exchange is two turns.

One input line is one user turn. Leading/trailing spaces and Unicode are preserved;
the terminal line-ending delimiter is removed by input(). Blank lines make no
request. Lines whose first non-space character is `/` are command syntax. Malformed
commands fail safely and the session continues. The default line mode has no
multiline editor; use the opt-in `--tui` described below.
EOF or Ctrl-C at input or during a request exits cleanly with status 0 and no
traceback. Ctrl-C does not resume the shell. A failed request prints a generic
sanitized error, discards that attempted turn, preserves prior successful history,
and permits retry or provider switching. Provider stderr and metadata are hidden.

`/history` displays the explicit conversation in memory; it does not redact
secrets users included in their own messages. `/clear` empties in-memory history
and, when saving is available, replaces saved turns with an empty history file.
There is no database or cloud sync.
Replies are display text: no shell/Git execution, code execution, file mutation,
tools, hooks, auto-approval, or routing is added. Existing provider transports
retain their established boundaries. Technical capabilities grant no permission.
Chat adds no orchestration, automatic skill/provider selection, retrieval, or
automatic A4 packet extraction, A5 Git injection, or A6 validation. A9 is available separately through orchestrate.

Existing `ask` remains the one-shot/pipeline interface. Bare `llmtool` retains its
subcommand-required behavior; automatic interactive entry is a future UX option.
In-memory history has no token budgeting or automatic truncation; provider
context limits may eventually cause a recoverable failure. The on-disk cap does
not bound the live session's in-memory history. Live smoke is NOT-TESTED.

### TUI UI/UX decision and planned polish (Owner accepted 2026-10-22)

See [the dedicated TUI UI/UX meeting](../../docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md) and the [A3.5 UX follow-up roadmap](../../docs/history/llmtool-roadmap-archive-20261028.md#a35-ux-follow-up--codexclaude-style-interactive-terminal-owner-request-2026-10-20).

**The TUI presents normal chat and also exposes separate PM and Task interfaces.** Current `llmtool chat --tui` supplies a scrollable conversation, multi-line composer, F1 help, keyboard navigation and honest status. Normal chat uses the same saved `ChatSession` history as line mode by default; `--no-history` disables only that history file, while PM and Task interfaces retain their independent persistence behavior. Closing the chat does **not** establish persistent Task resume or detached background work.

Agreed UI backlog, not yet fully implemented: **P0** command picker/completion for already-supported commands, input history search, composer polish and compact selectable status; **P1** Markdown/code/diff and expandable evidence display plus optional read-only Task/Agent/GitHub/Usage/Approval screens **only if Core exposes trustworthy data**; **P2** themes, syntax highlighting and optional layouts. Key mappings such as Shift+Enter, Ctrl+R, Ctrl+G and Ctrl+O are proposals subject to terminal testing and backwards compatibility. Unknown or unsupported functions are hidden/disabled or marked UNKNOWN; TUI changes must not add provider, PM, execution, Git, approval, token accounting, database or background-worker behavior.

### A3.5 UX-1/UX-2 — opt-in full-screen chat and truthful status

```sh
pip install -e 'tools/llmtool[tui]'   # optional extra: prompt-toolkit 3.0.x (BSD-3-Clause)
llmtool chat --tui
```

`--tui` is opt-in; plain `llmtool chat` is unchanged and never imports the UI.
Normal chat mode uses the same `ChatSession`, provider/skill rules, saved-history default and error rollback as line mode. The TUI also calls separate PM runtime and Task APIs; their records and execution permissions follow those Core interfaces and are not controlled by `--no-history`. Layout: a scrollable conversation pane, a
persistent multiline input, and a status bar. Keys: Enter sends; Shift+Enter (when
the terminal sends it), Option+Enter, Ctrl-J or Esc+Enter inserts a newline
(bracketed paste keeps newlines without sending); Up/Down on the first/last input
line recall earlier inputs from this process only; Tab
switches focus between input and conversation; PageUp/PageDown (Fn+↑/↓ on a Mac
keyboard) scroll the conversation one screen and the mouse wheel or trackpad one
row per wheel step (mouse reporting is on by default); F1 toggles key help; Ctrl-C
(or Ctrl-D on empty input) exits with status 0. Model output, reported model names
and errors are untrusted and rendered through the same control-character escaping
as TTY line mode.

macOS Terminal.app sends plain Enter for Shift+Enter by default. Use Ctrl-J or
Option+Enter there, or set Terminal → Settings → Profiles → Keyboard: add
Shift+Return → Send Text `\033[13;2u`.

The conversation pane keeps its own reading position, selection and follow state
(`chat_tui_view.py`), in screen rows rather than prompt-toolkit's caret-anchored
logical lines, so wrapped long lines and CJK text scroll one row per wheel step
without sticking or jumping. New output never moves a scrolled-back view (the
status bar shows `捲動 N% ⇣新輸出`); scrolling back to the bottom resumes following
(`最新`). A resize keeps the text that was at the top of the view. Dragging in the
pane selects text; dragging past the top or bottom edge keeps scrolling and extends
the selection. The selected text (CJK, wrapped lines, several screens) is copied to
the system clipboard with `pbcopy` (`wl-copy`/`xclip`/`xsel` elsewhere) while the
drag moves and again on release, and the status bar shows `已複製 N 字`, so Cmd+V
pastes exactly it: in Warp and Terminal.app, drag and then Cmd+V; no Cmd+C is
needed. Cmd+C never reaches the application in either terminal (Warp runs its own
Copy action, which with no Warp selection leaves the clipboard unchanged), and Warp
reports no mouse release when the button goes up outside its grid, which is why the
copy happens during the drag. Ctrl-C with a selection copies it again and clears it
instead of exiting. The pane has no scrollbar of its own. Shift-drag in Warp and
Fn-drag in Terminal.app select natively while mouse reporting is on, and Cmd+C then
copies that native selection. F6 hands the mouse back to the terminal (its own
select/copy; the wheel then no longer scrolls the pane).

In PM mode (`/pm connect`), the pane shows a working line (`PM 思考中… 12s`)
within a second of Enter, redrawn every second while the PM thinks or works and
replaced by the reply as it streams. Enter during a PM turn queues the draft (FIFO,
shown at once as `已排入佇列（第 N 則）`, count in the status row); each turn that
ends COMPLETED sends the next queued message, one turn at a time. A notice that
arrives while a PM reply streams is shown below that reply, never inside it. Esc
shows `已中斷，等待 PM 結束本輪…` at once and pauses the queue
(`已中斷；佇列已暫停（剩 N 則）…`): nothing more is sent, Enter on an empty draft
sends the next queued message (the rest stays paused), and a draft typed while
paused joins the end of the queue. A failed or refused turn, Ctrl-C or a disconnect
also pauses the queue: the pane lists what is waiting, `/pm queue send` resumes all
of it and `/pm queue clear` drops it (with the count). Messages still queued at exit
are printed to stderr.

One request is in flight at a time: Enter while busy keeps the typed input and
sends nothing; commands such as `/provider` are not run while busy. Esc, or the
first Ctrl-C, interrupts a chat reply: a provider that streams stops at its next
chunk and the text shown so far stays in history marked interrupted; a provider
that answers in one piece (all current providers) returns first, and that reply is
then shown marked interrupted. A second Ctrl-C marks exit pending, which happens
once the request ends or reaches the provider's own timeout. If prompt-toolkit 3.x is
absent, or stdin/stdout is not a terminal (pipes, redirects), `--tui` prints a
notice to stderr and runs the line chat before any UI starts. If the UI fails
after starting, it prints a sanitized error and exits 1; it never replays input
through the line REPL.

Status (status bar and `/status`) reports only what is known: the selected profile
and its configured model (`provider default` when none is configured); the
response-reported model only for `openai`/`openai-compatible` (CLI providers echo
configuration, so `UNKNOWN`); state `idle`/`working`/`error (<typed diagnostic
code>|unclassified)`, never inferred from message text (for example a rate limit
shows `PROVIDER_RATE_LIMITED` only from the typed provider diagnostic); elapsed time
measured locally with a monotonic clock; and provider-reported
`prompt_tokens`/`completion_tokens`/`total_tokens` validated as one consistent
triplet, otherwise `UNKNOWN` (missing, partial, bool, mismatched or failed
requests; no stale value from an earlier turn). Connectivity is `not probed`; no
VPN/API readiness is implied. `/clear` and a successful provider switch reset the
last-request status. Other reply metadata (reasoning, internal fields) is not shown.

See the [A3.5 validation report](../../reports/llmtool_a3_5_interactive_chat_validation_20261001.md)
for exact commands and evidence: 30 targeted tests PASS; canonical syntax check and
237 tests PASS, plus packaging and manual fake-provider UX validation.

## A4 — Evidence-first Context Packet

A4 is DONE and merged to main in `38b1a67`; external ChatGPT review PASS.
**Provider != Role != Agent != Skill != Context Packet.** Context Packet is data.

| Category | Semantics |
|---|---|
| FACTS | Directly supported observations from supplied material |
| EVIDENCE | Specific supporting material and declared provenance |
| INFERENCES | Supported conclusions that are not directly observed |
| HYPOTHESES | Possible explanations requiring confirmation |
| UNKNOWNS | Important missing information |
| VERIFY_NEXT | Specific next evidence to collect/check, as text only |

`llmtool/context.py` exposes frozen `ContextPacket` and `EvidenceItem`,
`parse_context_packet(text)`, `format_context_packet(packet)`, `to_dict()`, and
`to_json()`. Categories become tuples internally (list/tuple inputs accepted);
evidence uses typed immutable items. JSON uses arrays and objects in stable field
order; textual content, duplicates, uncertainty, and category membership are
preserved. The parser never promotes hypotheses or merges inference/hypothesis
categories. Structural validation does not certify factual support or provenance.

```json
{
  "facts": ["api-7 returned HTTP 500"],
  "evidence": [{"statement": "HTTP 500 at 10:00Z", "source": "supplied log", "locator": "line 1"}],
  "inferences": ["Request processing did not complete successfully"],
  "hypotheses": ["Startup configuration may be unavailable"],
  "unknowns": ["Previous container exception is not provided"],
  "verify_next": ["Inspect previous container logs"]
}
```

All six fields are required JSON arrays. Non-evidence items must be nonempty
strings. Evidence requires nonempty `statement` and `source`; `locator` is optional,
a nonempty string when supplied, or null. Serialization always includes locator
(null when absent); no locator/source is invented. Source examples include
supplied log, supplied diff, and user-provided incident notes. Locators can be
line numbers, resource identifiers, or diff hunks. They are recorded declarations,
not externally verified provenance.

```sh
llmtool context-pack --file incident.txt
llmtool context-pack --json --file incident.txt
```

Existing input sources and provider selection remain. The model receives the
A3 version-2 context-pack skill through the common message contract; its final
Reply.content is parsed and validated before stdout is touched. Default output
shows the six uppercase sections with evidence source/locator; empty categories
show `(none supplied)`. `--json` prints the stable JSON representation. The old
OBSERVATIONS/UNKNOWN/NEXT_EVIDENCE_TO_COLLECT prose format is replaced; scripts
consuming that format need to migrate. Plain prose from a model is now an error.

Parsing accepts only a JSON object, without code fences or trailing prose. A
1 MiB UTF-8 output cap and maximum JSON nesting depth 4 apply before decoding.
Missing/extra fields, wrong types, blank/non-string items, malformed evidence,
duplicate object keys, NaN/Infinity, invalid Unicode, unsafe text control
characters, excessive depth, and oversized output are rejected. Errors are
sanitized: malformed output exits 1 with empty stdout, including with --verbose,
and never echoes raw malformed response or provider metadata.

The module has no transport, file discovery, retrieval, tools, or execution.
VERIFY_NEXT command-like strings remain display/storage text, never actions.
Explicit --file input remains the existing caller-selected read; A4 gathers no
new evidence. No automatic chat extraction or /context command was added;
A3.5 chat remains plain multi-turn text, even if the context-pack skill is
explicitly selected (then replies follow its prompt but are not parsed by chat).
A5 collects offline Git evidence separately; A6 now checks structural support and command risk;
A9/A10 may reuse packets for orchestration/retrieval. None is implemented by A4.
User-supplied or model-generated sensitive text in supported fields is not
redacted or truth-checked; do not supply secrets. No hidden-reasoning field exists.

Validation: 29 targeted A4 tests PASS; canonical syntax check and 266 tests PASS,
with approved loopback access. Packaging and manual fake-provider JSON/human/error
checks PASS. Live model inference NOT-TESTED. Exact commands, times, environment
limitations and review are in the
[A4 validation report](../../reports/llmtool_a4_context_packet_validation_20261001.md).

## A5 — Git Intelligence

`llmtool git` collects one read-only snapshot from the current working directory;
Git resolves its repository root. It is offline and bypasses provider/config/key
resolution. No evidence is automatically sent to a model or injected into chat.

```sh
llmtool git
llmtool git --json
```

`llmtool/git_intel.py` exposes `collect_git_snapshot(cwd)` and
`format_git_snapshot(snapshot)`. Frozen typed models are `GitRepositoryInfo`,
`GitChange`, `GitStatus`, `GitDiff`, `GitCommit`, and `GitSnapshot`.
`GitSnapshot.to_dict()` / `.to_json()` provide stable field order and JSON arrays.
The snapshot contains root, branch, HEAD, detached state, staged/unstaged/untracked/
conflicted paths, changed/staged files, recent commits, both diffs, and related files.
Unborn repositories have null HEAD; detached HEAD has null branch.

Status uses porcelain v1 NUL records. Rename/copy destinations appear in category
lists, with their original path preserved in `status.entries`. Conflicts have their
own category. Lists sort paths and remove duplicates; `changed_files` is the union
of all four categories, including untracked paths. A path may be both staged and
unstaged. Spaces, Unicode, tabs and newlines are preserved. UTF-8 surrogateescape
preserves non-UTF-8 filename bytes where the host filesystem permits; JSON escapes
those surrogate code points. Human output quotes metadata and escapes terminal
controls in diffs; JSON/API preserve original diff text.

The runtime runner accepts only fixed argv for:

- `rev-parse --show-toplevel` and `rev-parse --verify --quiet HEAD`;
- `branch --show-current`;
- `status --porcelain=v1 -z --untracked-files=all --ignore-submodules=all`;
- `diff` / `diff --cached`, always with `--no-ext-diff --no-textconv --no-color
  --ignore-submodules=all --no-renames --src-prefix=a/ --dst-prefix=b/ --unified=3`;
- `log --max-count=10 --format=%H%x00%s -z --no-decorate --no-show-signature --no-notes`;
- two fixed read-only `config --null --name-only --get-regexp` queries to identify
  filter execution keys and partial/promisor configuration;
- `ls-tree -z HEAD`, `ls-tree -z HEAD:<validated-directory>`, and
  `ls-tree -z HEAD -- <validated-literal-directory>` for related paths.

No arbitrary Git arguments are accepted. Each command has a 10-second timeout,
1 MiB metadata stdout limit, and 64 KiB discarded stderr limit. Each diff has a
2 MiB byte limit: exceeding it fails the entire snapshot before stdout, with a
sanitized error. Successful diffs are complete (`truncated: false`) and include
`byte_count`. Log is capped at 10 SHA/subject entries; bodies are omitted.
Related files are tracked HEAD blob siblings in the first 10 sorted changed
parent directories, excluding changed paths, sorted and capped at 20. New
untracked directories have no HEAD siblings. There is no content scan, recursive
retrieval, semantic matching, or submodule traversal.

Security: argv lists and `shell=False`; explicit cwd; no pager/editor, hooks,
fsmonitor commands, external diff/textconv, or clean/smudge/process filter
execution. Filter command keys are blanked and required filters disabled before
status/diff. Optional index writes, automatic maintenance, inherited Git overrides,
and system/global Git configuration are disabled. Protocol access is blocked;
partial/promisor repositories are rejected to prevent lazy fetching. The trusted
local Git executable comes from PATH. Repository config/includes/attributes are
assumed stable during inspection; this is not a hostile filesystem sandbox or an
atomic snapshot. Filter-backed files may appear modified because conversion is
disabled. Global/system ignore rules are omitted, and diff content is not
secret-redacted. Git may read tracked file bytes to calculate diffs, but llmtool
never separately opens changed-file contents.
See [Git diff controls](https://git-scm.com/docs/git-diff),
[Git filter configuration](https://git-scm.com/docs/git-config), and
[Git environment controls](https://git-scm.com/docs/git).

Provider != Role != Agent != Skill != Context Packet != Git Intelligence.
A4 packets remain supplied/model-structured data; A5 supplies reusable local Git
observations without generating inferences or hypotheses. No automatic A4
conversion is introduced. A6/A9/A10 may consume this data later. Model-like text
in filenames/diffs is data only; no mutations, routing, execution, or chat changes.

A5 is DONE and merged to main in `7de6a99`; external ChatGPT review PASS.
Targeted: 39 tests (38 passed, 1 host-filesystem skip); canonical syntax + 305 tests
(304 passed, 1 same skip), exit 0. Wheel and manual fixture checks PASS.
Validation and host limitations are recorded in the
[A5 validation report](../../reports/llmtool_a5_git_intelligence_validation_20261001.md).

## A6 — Safety & Validator Layer

A6 adds deterministic, offline assessment in `llmtool/validator.py`.
**READ-ONLY != SAFE-TO-DISPLAY.**
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator.
No commands are run; no provider, credentials, Git collection, network, shell
configuration, alias resolution, or variable expansion is used.

```sh
llmtool validate command 'cat /etc/company/app/config.yaml'
llmtool validate command 'git status' --json
llmtool validate context --file packet.json
llmtool validate context --file packet.json --json
```

| Risk | Meaning | Examples |
|---|---|---|
| SAFE_READ | Recognized narrow metadata/read operation, without execution authorization | `pwd`, `ls -l`, `stat file`, `kubectl get pods` |
| SENSITIVE_READ | May display secrets or sensitive content even though read-only | `cat .env`, `env`, `printenv`, `kubectl get secret -o yaml`, raw `git diff` |
| MUTATING | Changes files, Git state, or services | `touch file`, `git commit -am test`, `git fetch`, `kubectl rollout restart deployment/api`, `terraform apply` |
| DESTRUCTIVE | Deletes data or disrupts systems/processes | `rm -rf /`, `git reset --hard`, `git clean -fd`, `git branch -D old`, `kubectl delete namespace production`, `terraform destroy`, `DROP TABLE users`, `kill -9 123` |
| UNKNOWN | Unsupported syntax/operation; safety has not been established | raw `git status`, `git log --oneline`, `git diff --stat`, `git show --stat`, `find /`, `grep -R /`, substitutions, custom commands |

Plain kubectl metadata reads assume a trusted kubeconfig/context and no connection/authentication/
impersonation overrides. --server/-s, token, kubeconfig, impersonation, TLS identity and related overrides
have at least UNKNOWN risk; recognized delete/apply/Secret reads retain their higher risk and require approval.

Sensitive patterns include `.env` files/globs, credential/secret/token names,
private key files (`.pem`, `.key`, `id_rsa`, `id_ed25519`), `~/.ssh`,
`~/.aws/credentials`, `~/.kube/config`, kubeconfig, credential stores/keychains,
`~/.netrc`, `~/.npmrc`, application.yml/yaml/properties and `/etc/.../config.yaml`.
`aws configure export-credentials` and secret YAML/JSON dumps are SENSITIVE_READ.
`kubectl describe pod`, full pod/deployment output, `systemctl status`, and all `ps`
forms are conservatively sensitive because they may expose environment, logs, or process
arguments. Recognized content-bearing raw Git reads (`git diff`, staged/cached diffs,
full `git log`, and narrowly recognized `git show`) are SENSITIVE_READ and require approval.

Recognized Git discard/delete forms are DESTRUCTIVE: default/worktree `restore`
(including `-W`), forced `checkout`, `switch --discard-changes`, and `stash drop`/`clear`.
Staged-only restore remains MUTATING. These are data classifications and grant no
execution permission. Unsupported/ambiguous restore syntax remains UNKNOWN.

Historical regression: `cat /etc/company/app/config.yaml` is **SENSITIVE_READ**.
Metadata alternatives `ls -l /etc/company/app/config.yaml` and
`stat /etc/company/app/config.yaml` are **SAFE_READ** within the narrow grammar.
Raw Git metadata reads such as `git status`, `git diff --stat`, `git diff --name-only`,
`git log --oneline`, and `git show --stat` are **UNKNOWN** and require approval because
repository Git configuration can run helpers even for apparent reads. Recognized
mutating/destructive Git operations retain MUTATING/DESTRUCTIVE. SENSITIVE_READ
preserves content-exposure warnings and requires approval; it does not claim execution safety. This differs from `llmtool git`,
which uses A5's fixed argv and safe Git configuration for explicitly scoped read-only collection.
This classification never authorizes execution or guarantees arbitrary file
contents/configuration are safe.

The classifier accepts at most **8 KiB UTF-8**. Controls and bidirectional formatting
characters cannot make a command safer: they are conservatively treated as separators with
an UNKNOWN risk floor, while recognized destructive components retain their higher risk.
It tokenizes simple commands and splits unquoted
`|`, `&&`, `||`, and `;`. Quoted separators remain argument data. Recognized risk
floors combine as DESTRUCTIVE > MUTATING > SENSITIVE_READ > UNKNOWN > SAFE_READ.
Unknown components never turn a compound into SAFE_READ. The result is the highest
recognized floor, not a complete analysis of unknown components. Output redirects
`>` / `>>` are MUTATING; `<` is sensitive for recognized secret targets, otherwise
UNKNOWN. Malformed quoting/operators are uncertain; recognized risky components
remain visible. Substitutions, variables, backticks, scripts, unsupported options,
and complex shell grammar remain UNKNOWN within that component. No expansion occurs.
Only conventional absolute binary locations and a plain `sudo <command>` form are
recognized; sudo applies an UNKNOWN approval floor, while sensitive/mutating/destructive
inner commands retain their higher risk. Other wrapper/global-option forms are unsupported. Glob matches are
never resolved. These are conservative heuristics, not a full shell parser or
complete security policy.

Frozen `CommandAssessment`, `ValidationFinding`, and `ValidationResult` provide
stable JSON dictionaries/arrays. Findings use INFO/WARNING/ERROR, static messages,
category and optional deterministic index locator. `classify_command()` preserves
original command text in the API. Command CLI output only includes risk/reason,
without echoing the command; exit 0 means assessment succeeded, **not** command safety.
`requires_approval(risk)` returns false only for SAFE_READ, true for everything else.
It is a separate future-policy helper, with no executor or approval UI attached.

`validate_context_packet(packet)` reuses A4 structure/UTF-8/1 MiB/depth limits and
adds a 1024-total-item bound before semantic processing. It reports ERROR for facts/
inferences with no evidence, an inference with neither fact nor evidence, and exact
statement collisions across facts/inferences/hypotheses/unknowns. Text comparison
is exact: no whitespace/case normalization, paraphrase matching, embeddings, or
LLM judgment. Unknowns without VERIFY_NEXT generate WARNING. `assess_verify_next()`
preserves every suggestion and assesses it; ordinary prose/unsupported/long
suggestions become UNKNOWN with warnings. Sensitive/mutating/destructive suggestions
also warn. These suggestions remain text and are never followed.

**STRUCTURAL SUPPORT != FACTUAL TRUTH.** Evidence presence does not prove a claim,
per-claim evidence linkage, or provenance accuracy. VALID means no ERROR findings;
warnings can include dangerous suggestions, and a packet may still be factually
wrong. No hypothesis is promoted into a fact; classification and source text are
preserved. JSON includes valid/findings/command_assessments in stable order; human
output starts VALID/INVALID and shows severity/code/message. Context CLI exits 1
for semantic errors after printing findings; malformed/oversized input exits 1
with empty stdout and sanitized stderr. Only the explicit regular --file is read,
bounded to A4's limit; no other evidence is collected.

A5 GitSnapshot validation is not added in this MVP. Existing ask, chat, context-pack,
and Git snapshot behavior remain unchanged; no automatic model-output validation.
A7/A8 may reuse results for feedback/regressions; A9/A11 must add their own permission
and approval enforcement before any future execution. No trainer, agent, routing,
retrieval, Telegram, repair loop, or production workflow is introduced.

A6 is DONE and merged to main in `63b5bae`; external ChatGPT review PASS.
40 targeted tests passed; canonical syntax and 345 tests (344 passed, 1 existing
A5 filesystem skip) completed with exit 0. Wheel and manual offline checks passed.
Exact commands, times, limits and security review are in the
[A6 validation report](../../reports/llmtool_a6_safety_validator_validation_20261001.md).

## A7 — Skill Trainer / Feedback Loop

A7 means **failure → human/external review → declarative skill proposal → regression
→ manual skill revision/version bump → validation**. It is not gradient training,
fine-tuning, RLHF, weight updates, embeddings training or automatic prompt mutation.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator != Feedback Case.
A7 is DONE and merged to main in `d61ca6a` via PR #6 after synchronization with
hardened main, complete local validation, external ChatGPT review and CI PASS. A3–A6 contracts and all packaged SKILL.md versions remain unchanged.

### Data models and strict format

`llmtool/feedback.py` exposes frozen, typed `FeedbackCase`, `SkillPatchProposal`,
`RegressionAssertion`, `RegressionCase`, `AssertionResult` and `RegressionResult`.
Lists supplied to models become detached tuples; public JSON uses arrays and
stable field order. `to_dict()`/`to_json()` return deterministic data; strict
`parse_feedback_case`, `parse_skill_patch_proposal` and `parse_regression_case`
reject missing/extra fields, invalid types/enums, duplicate object keys, NaN,
malformed JSON, unsupported controls and invalid UTF-8. JSON nesting is at most
6; serialized JSON/files at most 1 MiB; each model text at most 128 KiB UTF-8;
collections at most 128 items, regression assertions at most 64. Ordinary Unicode spaces (NBSP/U+3000)
are accepted; C0 except LF/CR/tab, DEL, C1, bidi controls and invalid UTF-8 remain rejected. A4 shares this rule. Versions are
positive integers up to 2,147,483,647; skill names use A3's validated naming rule.

FeedbackCase fields are `id`, `skill`, `skill_version`, `input`, `actual_output`,
`expected_behavior`, `failure_tags`, `validator_findings`, `review_notes`, `status`.
Input/expected behavior must be nonblank; actual output may be empty to capture
failed/empty replies. Original text, Unicode and whitespace are preserved.
Validator findings use A6's code/severity/message/category/nullable locator fields;
they are supplied explicitly through the reusable API, not inferred by the CLI.
No hidden reasoning, provider metadata/config, auth state or credentials are read.
Recognizable private-key headers, AWS access-key IDs, bearer tokens and OpenAI-style
keys and recognized credential assignments are rejected without echoing them.
Redacted placeholders (REDACTED, <redacted>, ***, null, none, placeholder) are allowed
for assignments. This is not comprehensive secret detection:
users must curate/redact all supplied material, including review notes, before
capture. `show` and case JSON intentionally display explicitly recorded text.

Explicit failure tags (no automatic inference/mapping):

```text
HALLUCINATION FACT_DROPPED UNSUPPORTED_CLAIM MISCLASSIFIED_FACT
MISCLASSIFIED_HYPOTHESIS MISSING_UNKNOWN MISSING_VERIFY_NEXT UNSAFE_COMMAND
SENSITIVE_READ_NOT_FLAGGED OVERLY_BROAD_COMMAND FORMAT_INVALID OTHER
```

IDs are the first 24 lowercase hexadecimal characters of SHA-256 over canonical
JSON (`sort_keys=True`, `ensure_ascii=True`, compact separators, `allow_nan=False`).
Feedback identifying fields are skill, skill_version, input, actual_output,
expected_behavior, ordered failure_tags and ordered validator_findings. Review
notes/status are excluded so review preserves ID; case edits to identifying fields
require a new ID. Regression IDs hash all fields except id, including ordered
source_feedback_ids/assertions. Parsed IDs must match content; IDs are never paths.

### Offline CLI and review workflow

```sh
llmtool feedback capture --skill context-pack \
  --input-file incident.txt --output-file model-output.txt \
  --expected-file expected.txt --tag FORMAT_INVALID --case-file case.json
llmtool feedback show --file case.json
llmtool feedback show --file case.json --json
llmtool feedback validate --file case.json --json
llmtool feedback review --file case.json --notes-file review.txt
llmtool feedback regression --case regression.json --output-file candidate.json --json
```

Capture loads the current packaged skill name/version and prints deterministic
case JSON. Omitting `--case-file` writes only stdout; selecting it creates a new
`.json` file, never overwriting an existing file. Tags may be repeated with distinct
values; duplicates are rejected. No automatic ask/chat/context-pack capture or
hidden storage exists. Repository-local curated fixtures are under `feedback/`,
separate from runtime skills and engineering skills; the CLI never scans them.

Show/validate require a known packaged skill and report CURRENT, OLDER_THAN_CURRENT
or NEWER_THAN_CURRENT. Older cases remain inspectable and valid historical data;
future versions are INVALID (exit 1). Cases are never silently upgraded.
Review records supplied human notes, advancing CAPTURED → REVIEWED only. External
ChatGPT review is human-mediated; the CLI does not contact ChatGPT or prove who
reviewed the case. `transition_feedback` permits exactly one forward step through
CAPTURED → REVIEWED → PATCH_PROPOSED → REGRESSION_READY → CLOSED, with nonblank notes
required after capture. Later statuses are explicit reviewer bookkeeping; no
automatic patch, regression creation or closure is triggered.

Explicit file operations use regular UTF-8 files only, nonblocking/no-follow opens,
anchored directory descriptors, and reject symlink components and `..` traversal.
Use physical paths (e.g. `/private/tmp`, not a symlink alias) when selecting temporary
data. Capture publishes atomically without overwrite and uses 0600 permissions.
Review atomically replaces only the explicit parsed case, preserving ordinary
permission bits and ID; content/identity checks detect intervening changes before
replacement. Concurrent writers are unsupported; this is not a transactional
multi-user store. Ownership/ACL preservation and cross-platform Windows support
are not provided by this POSIX file adapter. No directories are created implicitly.

### Proposals and version rule

`SkillPatchProposal` contains skill, from_version, proposed_version, rationale,
added_rules, changed_rules and linked_feedback_case_ids. A proposal requires at
least one rule change and unique nonempty case links. The proposed version must
be exactly from_version + 1; same/jump/downgrade versions are rejected.
`propose_skill_patch` requires a REVIEWED case matching the current packaged skill.
Proposal construction/parsing is a reusable API; it returns JSON data only:

```python
from llmtool.feedback import parse_feedback_case, propose_skill_patch

# case_json is explicitly supplied, already redacted case text.
reviewed = parse_feedback_case(case_json)
proposal = propose_skill_patch(reviewed, "Preserve missing evidence", added_rules=[
    "Keep missing previous exception in unknowns; never invent it."
])
print(proposal.to_json())
```

A7 never applies raw patches or modifies SKILL.md. A human separately edits/reviews
skills and increments the packaged version, then reruns tests/regressions. Linked
IDs record provenance; A7 does not search for linked cases or independently verify
review/approval artifacts. Proposal data alone does not authorize a change.

### Regression assertions and behavior

RegressionCase contains id, source_feedback_ids, skill, minimum_skill_version,
input and assertions. The recorded input is provenance, not sent to a provider.
Evaluation fails its precondition if the skill is missing or current version is
below the recorded minimum. Each assertion object requires exactly `type`, `value`
and `command` (nullable where unused). Unknown assertion types/fields are rejected.

| Type | Deterministic meaning |
|---|---|
| CONTEXT_VALID | value/command null; supplied output must parse as A4 packet and pass A6 without ERROR |
| FACT_CONTAINS / FACT_NOT_CONTAINS | Exact string element presence/absence in facts |
| HYPOTHESIS_CONTAINS / HYPOTHESIS_NOT_CONTAINS | Exact string element presence/absence in hypotheses |
| UNKNOWN_CONTAINS / VERIFY_NEXT_CONTAINS | Exact string element presence in the corresponding category |
| FINDING_PRESENT / FINDING_ABSENT | A6 finding code presence/absence |
| TEXT_CONTAINS / TEXT_NOT_CONTAINS | Literal substring presence/absence in supplied output |
| COMMAND_RISK_EQUALS | A6 risk equality; command null assesses the whole candidate after removing terminal CR/LF; otherwise requires the exact command as a candidate line or packet verify_next element |

Command selectors do not search arbitrary substrings or silently assess an absent
command. Commands remain data; classification grants no execution permission.
Malformed/unsupported structured candidates fail every structured assertion,
including negative assertions, with sanitized messages. Text/command assertions
can operate on plain output. Results preserve assertion order, index, type, boolean
and static message; they do not echo malformed output. JSON has case_id, passed,
results. Human regression output starts PASS/FAIL. Exit 0 means all assertions
passed; exit 1 means failed assertions or sanitized input/precondition/I/O error;
argument errors are exit 2. Malformed case/input errors leave stdout empty.

Curated [fixtures](feedback/README.md) cover historical
`cat /etc/company/app/config.yaml` = SENSITIVE_READ and metadata alternatives
`ls -l`/`stat` = SAFE_READ, plus HTTP 500 fact retention, configuration mismatch
remaining a hypothesis, missing previous exception retained as unknown, and
concrete verify_next/structural support. They reuse A4/A6, not duplicate rules.
Passing fixtures does not measure a live model or prove factual truth.

A7 has no provider/config/API-key resolution, network, subprocess, shell/Git
execution, eval/exec, arbitrary executable assertions, code loading, automatic
patch application or automatic skill writes. No live provider/VPN is needed.
A8 can later add benchmark/live-output collection separately; A7 adds no A8
scoring/analytics, A9 orchestration, retrieval or approval/execution engine.
Exact validation counts, packaging, environment limitations and manual CLI results
are recorded in the [A7 validation report](../../reports/llmtool_a7_skill_feedback_validation_20261001.md).
Historical pre-sync validation: 47 targeted tests; 392 discovered, 391 passed and
1 inherited skip. Current post-hardening synchronization: 47 A7 tests PASS; complete Python 3.13/3.9
suites each 423 discovered, 422 passed, one skip. Full results are recorded in
the validation report. No live provider call was made or required.

## A8 — Regression / Benchmark System

Benchmark measures outputs; it grants no authorization and is not an Agent.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator
!= Feedback Case != Benchmark. A0–A8 and hardening are merged to `main`; external
ChatGPT review PASS and A8-R1 is FIXED. Claude live model safety benchmark remains
FAIL as measured. A9 is now merged to `main`; see the A9 section below.

```sh
llmtool benchmark evaluate --case regression.json --output-file candidate.json --json
llmtool benchmark evaluate --spec benchmark.json --output-file candidate.json
llmtool benchmark suite --manifest suite.json --json
# Explicitly opt in to ONE request using an already configured named profile:
llmtool benchmark live --spec benchmark.json --provider local-claude --json
```

Evaluate and suite are completely offline, including with an empty HOME and no
provider configuration. They read only explicit regular UTF-8 files, using A7's
bounded/no-follow file adapter; symlink components and `..` are rejected. No stdin,
glob, recursive discovery, repository scan or implicit database. No suggested
command, VERIFY_NEXT item, regression input or patch is ever executed or applied.
Human output separates QUALITY, EFFICIENCY, RUNTIME and SAFETY, uses the shared
terminal-safe boundary, and does not dump the candidate. Stable JSON includes
static per-assertion results and metrics, not raw candidate text or arbitrary metadata.

### Specifications and suites

`BenchmarkSpec` wraps exactly one validated A7 RegressionCase without changing its
ID, assertion language or semantics. Strict JSON requires `regression` (the whole
A7 case), `name`, `tags` (array), and `safety` (null or the exact fields below).
`--case` accepts an A7 case (and the exact wrapper schema); `--spec` requires the
wrapper. Assertions, expected facts and findings remain solely in A7.

Safety fields are `forbidden_command_risks` (unique A6 risk names) and
`preferred_verify_next_any` (unique exact command strings). A safety expectation
requires a supported packet with no A6 ERROR, no forbidden risk, and at least one
preferred command when that list is nonempty. Classification uses A6 only; it is
DATA, never command execution or permission. Prose/oversized command suggestions
remain UNKNOWN. Sensitive suggestions can pass an explicit expectation that only
forbids mutation/destruction; that does not authorize displaying sensitive data.

A suite manifest requires exactly `entries`, an ordered nonempty array of objects
with `case_file`, `output_file`, and `runtime` (null or normalized runtime object).
File paths are relative to the manifest's directory, or explicit absolute physical
paths. Cases can be plain A7 cases or benchmark wrappers. Duplicate regression IDs
are rejected. Order is preserved. All cases validate before any stdout is emitted;
no partial successful JSON on a later input error. Aggregate ratios sum raw counts,
never average case percentages. Byte totals produce the aggregate compression
ratio. Runtime summaries give sample count, minimum/maximum/mean wall and provider
latency separately, and token totals only across available samples.

Frozen typed models detach collections into tuples. A7's strict parser rejects
missing/extra fields, duplicate keys, NaN/Infinity, invalid UTF-8/controls and depth
above 6. Files, specification/manifest/result JSON and candidate text: 1 MiB;
A7 input: 128 KiB; assertions: 64; suite: 32 cases; tags/preferred commands: 16;
name: 256 bytes; paths: 4096 bytes; preferred commands: 8192 bytes; packet: A6's
1024 total items. Full serialized suite results also must fit 1 MiB. No executable
objects, dynamic imports from data, hidden reasoning fields or arbitrary metadata.

### Metrics

Each quality dimension reports numerator, denominator, ratio and `assessed`.
Zero denominator gives null, never NaN. Malformed/unsupported structured output
fails A7's structured assertions, including negative assertions. Content metrics
then have `assessed: false`, zero observed numerator and null ratio; those are not
claims of measured absence or presence. Aggregate content ratios remain unavailable
if any contributing case with a nonzero denominator was unassessable.

| Dimension | Numerator / denominator |
|---|---|
| assertion_pass_rate | passed assertions / all assertions |
| fact_retention | matched FACT_CONTAINS / expected fact assertions |
| hallucination_rate | violated FACT_NOT_CONTAINS / curated forbidden fact assertions |
| finding_recall | matched FINDING_PRESENT / expected finding assertions |
| false_positive_rate | violated FINDING_ABSENT / curated forbidden finding assertions |
| hypothesis/unknown/verify_next retention | corresponding matched positive assertions / corresponding assertions |
| command_risk_pass_rate | matched COMMAND_RISK_EQUALS / command-risk assertions |
| context_validity | passed CONTEXT_VALID / context-validity assertions |

**hallucination_rate measures violations of explicitly curated forbidden facts only.**
It is not open-world factual verification. False-positive rate likewise measures
explicitly curated forbidden findings only. Exact A7 matching remains unchanged;
no universal factual detection, provider ranking or weighted intelligence score.

Efficiency uses exact UTF-8 bytes: `compression_ratio = output_bytes / input_bytes`.
Lower means a shorter output relative to the benchmark input. Empty input is rejected.
No token estimate is derived from bytes, characters or words.

Normalized runtime input requires exactly `wall_latency_ms`, `provider_latency_ms`,
`model`, `input_tokens`, `output_tokens`, `total_tokens`; each may be null. Supply
via evaluate's `--runtime-file` or a manifest entry's `runtime`. Offline latency
is absent unless explicitly supplied by the caller. These values are supplied data,
not authenticated observations. Timing is integer milliseconds in 0..86,400,000;
tokens are integers in 0..10^12, never booleans/negative/nonfinite values. Explicit
usage requires all three consistent counts (`total = input + output`).

Only live resolves normal provider/config precedence and credentials. It reuses
A3 `messages_for('ask', case.input, explicit_case_skill)` and calls Provider.complete
exactly once, without retries, skill auto-selection, command execution or recording
files. Whole-request latency uses `time.monotonic()` and is separate from normalized
provider `elapsed_seconds`. Model identifiers and bounded elapsed/token fields are
the only metadata retained. OpenAI input/output tokens map directly; compatible
prompt/completion tokens map to input/output. The provider's valid total is retained.
Incomplete, malformed or inconsistent provider token triplets become entirely
unavailable. Claude/Codex elapsed-only metadata therefore gives null token counts
and `token_usage_available: false`, never fabricated zeros or cost estimates.
Zero counts from a complete valid triplet remain available. Token totals are not
monetary cost. Live provider failures remain sanitized and raw stderr is omitted.

Exit 0: all A7 assertions and explicit safety criteria pass. Exit 1: evaluated
failure or sanitized input/runtime/file error. Argparse usage: 2; interruption: 130.
Malformed input produces empty stdout. A malformed candidate can produce a failed
result when within the text bound; this distinguishes scoring failure from invalid
case/manifest/runtime data. Existing A7 text-only assertions can evaluate prose;
only an explicit safety expectation requires a structurally/semantically valid packet.

Synthetic historical permission-denied fixtures and examples are in
[benchmarks/README.md](benchmarks/README.md), excluded from wheels. The fixture
allows historical sensitive cat as classified data but requires narrower metadata
evidence and forbids MUTATING/DESTRUCTIVE suggestions. See the
[A8 validation report](../../reports/llmtool_a8_regression_benchmark_validation_20261001.md)
for exact current tests, packaging, optional live smoke and CI evidence.


## A9 — Agent Orchestration

A9 adds a bounded sequential text orchestration layer. **Role != Provider**: each
plan step explicitly binds one built-in role (`grinder`, `validator`, `coder`,
`reviewer`) to one named provider profile. Provider/model names never imply role.
An AgentStep is role + explicit profile + bounded text handoff; it is not a permission
grant and does not enable provider-native tools.

```sh
llmtool orchestrate --plan plan.json --dry-run --json
llmtool orchestrate --plan plan.json --file task.txt
llmtool orchestrate --plan plan.json "Review this supplied incident"
```

The A9 parser has a hard cap of 8 steps; with the current four built-in unique roles the effective MVP maximum is 4 steps. It is fail-fast:
no retries, fallback provider, hidden substitution, fan-out, voting, loops, branches,
or dynamic routing. Every live step resolves its explicit profile with
`resolve_profile_config(profile_name, require_configured=True)` before credential value resolution.
Only trusted persisted user-profile data applies: cwd project overrides and generic LLMTOOL_PROVIDER /
LLMTOOL_MODEL / LLMTOOL_BASE_URL / LLMTOOL_TIMEOUT / LLMTOOL_MAX_TOKENS cannot rewrite a plan binding.
The selected profile's configured api_key_env still reads its credential value after readiness. Normal ask/config precedence is unchanged.

Handoff is shallow and bounded: every step receives the original task plus only the
immediately previous role output. Earlier outputs are not accumulated and there is
no hidden long-term memory. Previous role output is marked as untrusted model text; delimiters reduce ambiguity but are not claimed to prevent prompt injection.
A10 is a separate explicit retrieval command; A9 does not automatically retrieve context.

A9 is text-only. It never executes shell/VERIFY_NEXT/Git, edits files or code, applies
patches, runs tests, or treats model words such as "approved" as authorization. The
LLM `validator` role is a reasoning/review responsibility and cannot override
deterministic A6 command-risk results. A8 remains the measurement layer; A9 does not
automatically run benchmarks or rank providers.

`--dry-run` validates only the explicit plan structure and is offline: it does not
read task input, config, credentials, instantiate providers, or access network.
Repository examples live under `orchestration/` and are not packaged.

Anthropic HTTP API remains future A2.3 work. A9 has no Anthropic special case; a
future registered provider can use the same explicit profile contract. A9 is DONE and merged to `main` via PR #10 (`8776476`). A9-R1 is FIXED; updated external ChatGPT review PASS and final-head CI PASS. A10 is DONE and merged via PR #12; see its section below.

A9 bounds: original task <= 1 MiB UTF-8; each output <= 128 KiB UTF-8; summed
step content <= 512 KiB; serialized result <= 1 MiB. Each completed prefix must
fit the result JSON budget (including escaping and repeated final_content) before
the next profile is resolved. Overflow stops the pipeline without truncation or
partial stdout. Human output escapes controls on TTYs; JSON and redirected text
retain generated/user-supplied material. Roles do not automatically select skills;
role + explicit skill composition remains future work. See the
[A9 validation report](../../reports/llmtool_a9_agent_orchestration_validation_20261001.md).

## Post-A9 Hardening — DONE and merged to main

A0–A9 remain DONE and merged. Post-A9 hardening is DONE and merged to `main` via PR #11
(`5f581b46968a2dd09d808b3d3125beb4597c0fc1`) after external ChatGPT review PASS and Claude
re-review APPROVE. Final reviewed branch head `566cdb518ce7c3a3b1b3bbff888fe25a2827178f`
passed Python 3.9 / 3.14 CI with 552/552 tests in each job. M1/M2/M3/M4/M5/M7, R1–R8,
and the final F1 credential-source gap are fixed within the documented bounded contracts.
Unsafe analyze-log NEXT_COMMANDS suggestions are visibly suppressed as text; no suggested command is executed.
M6 preferred_verify_next_any matching remains DEFERRED for a separate design review; exact A8 criteria and
the historical Claude model safety FAIL remain preserved. A10 status is documented below. Evidence:
[hardening validation report](../../reports/llmtool_post_a9_hardening_validation_20261001.md).

## A10 — Dynamic Context Retrieval — DONE and merged

A10 provides one bounded path-manifest retrieval round, independent of A9 roles.
Task → deterministic candidate manifest → planner → selected file reads →
synthesizer → strict A4 ContextPacket. Exactly two sequential Provider.complete
calls on success; no retry, fallback, recursive retrieval, parallel calls, hidden
profile substitution, or automatic A9 integration. Provider != Role != Retrieval Planner.

~~~sh
llmtool retrieve-context \
  --root . \
  --scope tools/llmtool/llmtool \
  --scope tools/llmtool/tests \
  --planner-provider office-qwen \
  --synthesizer-provider office-qwen \
  "Why can explicit orchestration profiles still be overridden?"

llmtool retrieve-context --root ./synthetic-evidence --scope auth.py --scope db.py \
  --planner-provider local-profile --synthesizer-provider local-profile \
  --file task.txt --json
~~~

Both profiles must explicitly exist in trusted persisted user configuration.
Generic LLMTOOL_* request overrides and project .llmtool.yaml cannot rewrite
either binding. Readiness precedes configured api_key_env value access. The
planner and synthesizer profiles are both readiness-validated before any input read
or provider request. No credentials are read during preflight. Synthesizer credential
access/client creation remain deferred until all retrieval succeeds.
Normal ask/config precedence remains unchanged. Explicit-input queued pipe/socket
conflicts are checked before config; stdin-only reading starts after BOTH profiles
pass readiness. Existing closed-stdin, regular-file shell-loop and empty-pipe behavior applies.

### Authorization and data sent to providers

Root and at least one scope are required; no default plan, HOME scan, repository
discovery, parent traversal, Git history or implicit extra evidence. Each scope is
a relative regular file or recursively scanned directory. Overlapping scopes are
deduplicated, and manifest paths are sorted, relative POSIX paths.

**Choosing root + scopes authorizes selected files in those scopes to be sent to
the configured synthesizer provider. Choose narrow scopes.** The planner sees
only the original task, candidate path names and byte sizes; it sees no candidate
contents. Its entire response must be exactly strict JSON of the form:

~~~json
{"files":["relative/path/a.py","relative/path/b.md"]}
~~~

The only key is files, containing 0..8 unique exact manifest paths. Empty selection
is valid and still proceeds to the synthesizer with the task. Duplicate JSON keys,
constants, extra fields, malformed/fenced/prose output, duplicate/unknown/noncanonical
paths and excessive selection fail before any selected content is read or any
synthesizer credential/client access. Model output never grants authorization.

The synthesizer receives structured JSON with the original task and the selected
documents' relative paths, normalized UTF-8 content and control_escaped flags.
Ordinary safe UTF-8 text is passed unchanged. Unsafe C0/C1/bidi controls are
deterministically escaped into visible text (ESC becomes a literal backslash + x1b),
never removed or interpreted as ANSI. LF/CR/TAB and normal Unicode formatting
including ZWJ/ZWNJ are preserved. NUL and invalid UTF-8 still fail. Filenames, task and documents
are untrusted data, not instructions. No absolute root or host paths are included
by A10. User-supplied task or file content may itself mention host paths.

### Filesystem policy and bounds

A10 uses POSIX anchored descriptors, no-follow/nonblocking opens and metadata
identity checks before/open/after reads. macOS/Linux support requires their
no-follow directory APIs; unsupported platforms fail closed. Root and every
ancestor must be physical directories (use /private/tmp rather than its symlink
alias /tmp). Symlink scopes are rejected; nested symlink files/directories and
nonregular entries are excluded, never followed. Detected file replacement,
changes, or symlink substitution fail before synthesis. This is not an atomic
snapshot or protection against every hostile concurrent filesystem change.

.git content is excluded, including a root inside .git. Obvious credential stores
(.ssh, .password-store, .aws, .kube, .docker, .gnupg, .azure,
.config/llmtool, .config/gh and .config/gcloud)
are excluded, including roots inside them. Credential files excluded even in
authorized scopes include .env/.env.*, .netrc, .npmrc, .pypirc, .pgpass,
.git-credentials, .vault-token, id_rsa/id_dsa/id_ecdsa/id_ed25519 and
.pem/.key/.p12/.pfx files. Known etc/shadow, etc/gshadow and proc/<pid>/environ
locations are excluded even when root points inside their parent directories.
Additional narrow exclusions: .cargo/credentials, .cargo/credentials.toml, .htpasswd,
kubeconfig, credentials.json, *.tfstate and *.tfstate.backup. No broad config/secret/
credentials substring rule is used. There is no allow-sensitive escape hatch.
Ordinary source names such as config.py, configuration_test.py and secret_manager.py
remain candidates. Path text rejects C0/C1/bidi controls and invisible format
characters; file contents allow ordinary Unicode formatting such as ZWJ/ZWNJ
while visibly escaping unsafe C0/C1/bidi controls; NUL and invalid UTF-8 are rejected.

| Bound | Maximum |
|---|---|
| Task UTF-8 | 1 MiB (shared source reader has an outer 16 MiB bound) |
| Explicit scopes | 32 before deduplication |
| Relative path UTF-8 / path depth | 4096 bytes / 64 components |
| Scanned directory entries, including excluded entries | 16,384 |
| Manifest regular files | 4096 |
| Compact serialized manifest UTF-8 | 256 KiB |
| Planner response UTF-8 / JSON depth | 64 KiB / 2 |
| Selected files | 8 |
| Each selected file / summed evidence (original bytes) | 256 KiB / 512 KiB |
| Normalized content UTF-8 | at most 4x original bytes |
| Structured provider user payload UTF-8 | 4 MiB including JSON escaping |
| ContextPacket | existing A4 1 MiB, depth 4; A6 1024 items |
| Public result JSON UTF-8 | 2 MiB including packet, metadata and escaping |

Manifest construction reads metadata only; it never reads initial bytes to identify
binary data. Regular files larger than 256 KiB are excluded, not truncated.
A narrow suffix exclusion covers .png/.jpg/.jpeg/.gif/.webp/.ico/.pdf/.zip/.gz/
.bz2/.xz/.7z/.woff/.woff2/.ttf/.otf/.mp3/.mp4/.mov/.sqlite/.sqlite3 (case insensitive).
Extensionless and ordinary source/data files remain eligible. Unknown/misnamed
binary files may still be selected and fail bounded UTF-8/NUL validation.
Planner instructions state at most 8 files, each candidate <=256 KiB, total selected
byte_count <=512 KiB, intended UTF-8 evidence and exact manifest paths only.
Selection's declared total is checked before read_selected; read-time checks remain.
Exceeding a scan/count/manifest budget
fails clearly and asks for narrower scopes; nothing is silently truncated.
Selected files are read at most 256 KiB + 1 each. Binary, oversize or changed files
fail before synthesizer credentials/client access. Message/result caps account for
JSON escaping and preserve the inherited packet bound.

### Packet provenance and output

The synthesizer must output strict A4 JSON; parse_context_packet and A6 structural
validation remain authoritative. Every evidence source must exactly equal task
or an actually retrieved relative path. Invented sources fail, without repairing
model output. This checks the supplied source set, not whether each claim is true
or properly supported by that source. VERIFY_NEXT remains text only, never executed.

Frozen result data includes planner/synthesizer profile names, scopes, selected
path/byte_count/SHA-256/control_escaped metadata, A8 allowlisted runtime metadata and the final
packet. byte_count and SHA-256 always describe ORIGINAL file bytes, even when
control_escaped=true records that model-facing content was transformed. Public
result never includes raw document contents. Hashes are audit/provenance only, not authorization. No raw retrieved
documents or raw provider objects are duplicated in result JSON; the generated
packet can quote evidence and therefore can contain selected material.
Unavailable token counts stay null, without estimation or quality/provider ranking.

Human output shows retrieved metadata and the packet through the existing
terminal-safe boundary. JSON preserves generated/user-supplied packet data.
All stages validate before stdout; failures emit sanitized stderr and nonzero
exit, with no partial result. A10 never executes shell/Git/commands/tools, edits
files, applies patches, runs tests, searches the web, or discovers credentials.

A10 MVP selects from paths/sizes, not semantic search. It may choose the wrong
files (the independent review observed missed log_safety.py selections). Use narrow
scopes and explicitly name likely relevant files in the task when known. There are
no embeddings, indexes, grep/ripgrep, AST search, vector DB,
recursive loops or file watchers. Explicit scope cannot prove that ordinary source
contains no secret. The narrow deny policy is defense in depth, not comprehensive
secret detection; it cannot detect secret content or hard-link aliases. Sent task
and evidence are subject to the selected provider's normal data handling.

A10 is DONE and merged via PR #12 (`21b9757`); Claude re-review APPROVE, final VPN acceptance PASS.
There is no --save-output option: A10 remains read-only, with no malformed-output
file writes, retention/overwrite contract or raw diagnostic dump. Errors remain
sanitized with no partial stdout. Output retention needs a separate future design.
A11a.1–6 merged; A11a.4 PR #19 merge `2bade6a` / historical independent and
external source review PASS / post-merge main CI `37167325001` PASS;
A11a.5 PR #22 merged as `da027f0`, post-merge CI `37193806074` PASS (3.9/3.14, 824 each);
A11a.6 PR #24 merged as `6b7809a`, post-merge CI `37213447952` Python 3.9/3.14 PASS;
A11a.7 final acceptance merged via PR #25 (`e3869828`); post-acceptance B1–B7 bugfix merged via PR #26 (`2761ce10`) with post-merge CI `37228032137` PASS; overall A11a DONE;
A12 remains future optional frontend; Anthropic HTTP API remains
future A2.3; M6 remains DEFERRED. Validation is recorded in the
[A10 validation report](../../reports/llmtool_a10_dynamic_context_retrieval_validation_20261002.md).

## A11b.1 — Typed Action & Approval Contract — current data-only slice

Provider != Role != Agent != Skill != Action != Approval. None of these boundaries
converts model output or provider capability into approval authority.

`llmtool action validate` and `llmtool approval validate` validate strict version-1
proposal and receipt data offline. They read only explicit bounded JSON inputs;
there is no config/credential lookup, provider request, Git invocation, write,
executor, approval prompt/issuer, automatic approval, retry or recovery. A successful
parse is a **PROPOSED** action. A matching receipt remains **NOT_AUTHORIZED**.
Exit 0 means the requested structural check passed; it grants no permission.

```sh
llmtool action validate --file action.json --json
llmtool approval validate --action action.json --approval approval.json \
  --state state.json --nonce 8888888888888888888888888888888888888888888888888888888888888888 \
  --now 150 --json
```

`--state`, `--nonce` and `--now` are mandatory caller-supplied synthetic inputs.
`--consumed-nonce HEX` may repeat up to 128 times for a synthetic consumed-set check.
No real execution state is collected. The output explicitly says
`state_checked=SUPPLIED_ONLY`; all CLI results have `authorized=false`.
Malformed contract/input errors return 1 with empty stdout and fixed stderr;
binding/state/expiry/nonce failures return 1 with bounded validation metadata.
Usage errors return 2. There are no execute/apply/yes/grant options.

The reusable `llmtool.action_contract` API exposes frozen `Action`, `Approval`,
`TaskBinding`, `RepositoryBinding`, `FileMetadata`, `FileState`, `FileExpectation`,
`RepositoryState`, three typed payloads, `ApprovalValidation` and `AuditEvent`.
Collections detach into tuples; serializers return detached dictionaries/arrays.
`parse_action`, `parse_approval` and `parse_repository_state` accept only exact
fixed fields. No generic payload dictionary, raw content, shell, argv, executable,
provider/tool field or display text can acquire authority.

| Kind | Exact payload fields | Constraints |
| --- | --- | --- |
| FILE_CREATE | target, new_content_sha256, new_size, new_mode | Target is ABSENT; content is digest-only |
| FILE_WRITE | target, new_content_sha256, new_size, new_mode | Target is REGULAR with complete expected state |
| FILE_DELETE | target | Target is REGULAR with complete expected state |
| GIT_OPERATION | operation, files, expected_index_sha256 | operation is ADD only; every path has complete REGULAR state |

COMMAND and COMMIT are deliberately deferred. PUSH, RESET, CLEAN, checkout,
config, hooks/helpers and arbitrary subcommands are unsupported. A future command
contract needs a reviewed fixed executable/typed argv, credential-free environment
policy, wall-clock/output limits and explicit sandbox/network controls. This slice
does not derive command semantics from A6 classifier text or model output.

Action requires exactly `schema_version`, `action_id`, `created_at`, `kind`, `task`,
`repository`, `scope`, `payload`. schema_version is integer 1; action_id/task_id are
1–64 ASCII identifier characters starting with a letter or digit, then letters,
digits, dot, underscore or hyphen. Task requires task_id, run_id (32 lowercase hex),
contract_version (1..2^31-1), contract_digest (SHA-256). These are bindings to a
caller-owned pinned contract, not an implemented Task Contract engine.
Repository requires repository_id (caller-assigned SHA-256 identity), canonical_root
(absolute canonical literal POSIX spelling), root_device, root_inode, expected_head
(40 or 64 lowercase hex). The root is a declaration of a physical repository;
this module never resolves or proves that declaration, its identity or a sandbox.
An embedding trusted caller must independently establish repository identity and
supply the physical-root/HEAD/file/index snapshot. A root string cannot authorize
access or protect the host filesystem.

Scope is 1..32 sorted unique **exact relative file paths**, not recursive directories.
Each payload target must occur in scope. A target contains exactly path and state.
State contains exists (boolean), file_type (ABSENT or REGULAR), content_sha256,
metadata. ABSENT means exists=false, digest=null and metadata=null. REGULAR requires
exists=true, SHA-256 digest, and metadata with device, inode, mode, uid, gid, size,
mtime_ns and nlink=1. Mode is 0..0777; special bits/hardlinked/symlink/device states
are unsupported. New content is never stored: only its digest, byte count and mode
are proposed. RepositoryState requires version, repository, files and index_sha256;
index is null for file proposals and a supplied digest for Git ADD.

Canonical SHA-256 hashes the UTF-8 bytes of compact sorted-key JSON
(`ensure_ascii=False`, `separators=(',', ':')`, `allow_nan=False`) with this envelope:
`{"domain":DOMAIN,"value":VALUE,"version":1}`. DOMAIN is `llmtool.action`,
`llmtool.action-state` or `llmtool.approval`. VALUE is the complete exact fixed
serialized object, including its schema_version (Action/Approval) or version (state).
The digest envelope, RepositoryState and AuditEvent keep `version`; Action/Approval
reject the legacy `version` key. No normalization, coercion, omitted
execution fields or authoritative display text exists. Whitespace/key order in
input JSON do not affect the digest. All action fields affect its digest.

Approval requires exactly schema_version, source, action_digest, action_kind, task,
repository, scope, state_digest, issued_at, expires_at and nonce (64 lowercase hex).
The only accepted source spelling is USER, **a claimed source, not proof of issuance**.
Issuance must not precede action.created_at; expires_at-issued_at is 1..3600 seconds.
Validation requires issued_at <= supplied now < expires_at and exact nonce equality.
No separate Action expiry is needed here: Approval carries the bounded validity
window; changes to created_at invalidate its bound action digest.
The expected-state digest binds the repository and every expected file/index field;
any supplied state mismatch fails closed. These are synthetic equality checks,
not actual filesystem/Git rechecks or prevention of a concurrent TOCTOU attack.

`validate_approval(..., trusted_context=None)` reports bindings separately from source
attestation. `TrustedApprovalContext(approval_digest)` is a **caller attestation
precondition**: a trusted embedding host must authenticate the actual user's exact
receipt decision before supplying it. There is no context parser, CLI option, issuer,
signature verifier or authentication implementation. Constructing this object from
untrusted model/JSON data violates the API's trust precondition. Even matching
USER_ATTESTED results have authorized=false because execution policy is absent.
Model text saying “approved”, provider capability and a USER field never grant authority.

A nonce field alone is **not anti-replay**. The supplied consumed set is not persisted
or atomically updated; repeated validation succeeds again with the same unconsumed
inputs. A durable replay ledger and trusted nonce issuance/consumption remain future
requirements. Audit types PROPOSED, GRANTED, REJECTED, EXPIRED and INVALID_CONTRACT
contain only fixed enums/digests. GRANTED records a caller-attested USER decision;
it is not execution authorization. CLI can never emit GRANTED. Audit data is not
persisted/durable/authenticated evidence and has no executed/rollback/resume flow.

| Bound / strictness | Rule |
| --- | --- |
| JSON and explicit input file | 64 KiB UTF-8; nesting <=6, total values <=1024 |
| Files/scopes | <=32; each and summed expected regular-file size <=1 MiB |
| New content size | <=1 MiB; digest only |
| Paths/root | <=1024 UTF-8 bytes and <=32 components; literal letters/digits/dot/underscore/hyphen |
| Numeric metadata | strict integers 0..2^63-1; inode >0; size/mode narrower as above |
| Times | strict epoch seconds 0..253402300799 |
| Parser | Unknown/missing/duplicate keys, nonfinite/floats, invalid UTF-8, C0/C1/DEL/Cf controls and coercions rejected |

Paths reject absolute targets, dot/parent traversal, empty components, leading-dash
components, shell punctuation/expansions, backslashes, globs and obvious credential/
Git storage paths. Explicit input files use a separate literal POSIX spelling policy:
spaces, `+`, `@`, `~`, leading `-`, and ordinary legal components (including names
forbidden as Action targets) are accepted without shell/environment/home expansion.
`./action.json` is accepted; explicit `..`, empty components, controls/invisible
formats and invalid UTF-8 spelling are rejected before normalization. Original and
absolute input paths are bounded to 1024 UTF-8 bytes and 32 components. Inputs require
a `.json` suffix, physical paths outside `/dev`, `/proc` and `/sys`, no-follow
ancestor/leaf opens, single-link regular files and bounded before/after identity checks;
symlinks, FIFO/device paths, oversize and changed inputs fail safely. Path/filesystem
and read UTF-8 failures return `ACTION_INPUT_INVALID`; malformed JSON/schema/content
returns `ACTION_CONTRACT_INVALID`, both with empty stdout and sanitized stderr.
Closed stdout pipes return 0 without shutdown diagnostics. Normal read access
can update OS atime; no product write operation is performed. Unsupported input-reader
platforms fail closed. The pure contract API itself performs no filesystem access.

A11b remains IN PROGRESS. Executor, real state preflight/TOCTOU protection, OS sandbox,
trusted issuance/UI, replay ledger, durable audit-before-mutation, Git hardening,
rollback, bounded repair and provider data-export policy remain deferred and require
QG-S1 controls and separate design/review. A11c.1 S1a offline inspection is implemented
and accepted via PR #128; S1b A is fake-only, real handoff BLOCKED pending B; full production integration
and A11c.2 are NOT STARTED. A12 is future optional.
Cloud/offline tests and packaging are required for this slice; no Mac/live provider,
VPN/auth/session or real execution acceptance is required or claimed.
See the [A11b.1 validation report](../../reports/llmtool_a11b1_action_approval_contract_validation_20261005.md).

## A11b.3 D1 — Offline policy decision and one-use receipt ledger (synthetic only)

D1 (Issue #97) adds `llmtool.authorization_policy` and `llmtool.authorization_ledger`.
They decide and record **fake** grants only. There is no CLI, model-JSON or parser route
into them, no production issuer and no real effect. `ApprovalValidation.authorized`
stays `False`.

- **Decision.** `decide()` returns `DENY`, `WAITING_APPROVAL` or
  `ALLOW_WITH_TRUSTED_RECEIPT`, shown as `DENIED`, `NEEDS_APPROVAL` or
  `FAKE_POLICY_ALLOWED`, never `APPLIED`. It only supports a synthetic `FILE_CREATE`.
- **What it takes to ALLOW.** All of the following must hold:
  - a host-injected `HostIssuer` of class `POLICY_CONTRACT` vouches for the exact
    immutable `PolicyContract` digest;
  - the action is inside that contract;
  - the host-observed state matches: repository, HEAD, target absent.

  A model-written contract, `source=USER`, `trusted=true` or a receipt id grants
  nothing. Routine contracted actions do not ask the owner.
- **Owner P0 and separate authority.** Owner P0 is exactly the three classes in
  `skills/ai-team-delivery`: product mismatch, admin/root, macOS system. These return
  `WAITING_APPROVAL`, including in non-TTY/background use. New cost and new data export
  are separate authorizations: they `DENY` with `SEPARATE_AUTHORIZATION_REQUIRED` and
  are never implied. An out-of-contract action is `DENY` (`OUT_OF_CONTRACT`), not P0.
  Only an action inside a host-vouched contract can reach the P0 wait; an untrusted,
  unvouched or out-of-contract request is denied, not escalated to the owner.
- **Receipt binding.** A receipt is bound to:
  - task, run and contract;
  - the action digest, kind, paths and content;
  - repository identity, HEAD, index and worktree;
  - policy id, version and digest;
  - issuer class and capability;
  - the redeeming run;
  - the effect kind;
  - the parent directory identity, target absence, expiry and nonce.

  Redemption rechecks a fresh host-observed state.
- **The ledger.** It is SQLite with a rollback journal, `synchronous=FULL` and
  `fullfsync=ON`. A receipt moves `ISSUED → CONSUMED → EFFECT_DONE | EFFECT_UNKNOWN`
  (or `DENIED` / `EXPIRED`). Consumption is a conditional update in `BEGIN IMMEDIATE`,
  committed before the sink runs.
- **Crash, replay and race behavior.** This is at-most-once, not exactly-once:
  - A crash before or after the fake effect leaves `CONSUMED`. `recover()` turns that
    into `EFFECT_UNKNOWN`, which needs a fresh decision.
  - Replay, restart and a second racing process are refused.
  - One decision binding yields at most one receipt in any status; a new run needs a new
    decision.
- **Fail-closed store checks.** The store must be an existing regular 0600 file owned
  by the euid, with a single link, opened with `O_NOFOLLOW`. Its directory must not be
  writable by group or other. The file and its directory (type, owner, mode,
  device/inode) are rechecked on every transaction, after the write lock is held, and the
  host clock is read only once that lock is held.
  These all fail closed:
  - a missing, symlinked, swapped, corrupt or locked store;
  - a host clock below the durable high-water mark.
- **Limits.**
  - Power-loss durability on macOS is NOT_VERIFIED.
  - APFS mode, symlink and lock semantics need native Mac evidence; Linux CI does not
    prove them.
  - Nothing here protects against malicious code running as the same user.
  - `redeem()` does not re-check policy revocation: a receipt is trusted for at most its TTL
    (≤ 1 hour).
  - The host must place the store outside every repository root and worktree; D1 does not
    enforce this.
  - Real repository effects (F2) remain NOT AUTHORIZED.

## A11b.5 V1 — Offline task verification and run budgets (data only)

This is a first, partial A11b.5 slice. It adds two pure modules with no CLI and no
executor, and changes no run status, schema or exit code. **A11b.5 is not complete:**
recovery policy, real evidence producers and runtime enforcement still need an executor.

- **`llmtool.task_verification`.**
  - It checks a pinned `TaskContract` (version, digest, scope, acceptance criteria)
    against caller-supplied `EvidenceRecord`s and returns `PASS`, `FAIL`, `INCOMPLETE`
    or `NOT_TESTED`.
  - The verdict is deterministic and does not depend on evidence order.
  - Any of these keeps the result from being `PASS`:
    - missing, interrupted, partial or not-run evidence;
    - evidence for another contract version, run or subject.
  - A model's own success claim (`MODEL_CLAIM`) never satisfies a criterion. It is
    recorded and ignored, so complete independent evidence can still give `PASS`.
  - Any failed criterion makes the result `FAIL`. An unpinned contract evaluates nothing.
  - A `VerificationResult` whose status contradicts its criteria or findings is
    rejected on construction. A well-formed result is still not proof of a review.
  - This is **structural verification only**: it cannot prove a command ran.
    `VERIFIED ≠ DONE`, and `authorized` is always `False`.
- **`llmtool.run_budget`.**
  - It holds explicit call, repair (at most 2), wall-clock and input/output token budgets.
  - Usage covers preparation, retry, repair, escalation and review calls.
  - Unknown usage or elapsed time is `UNKNOWN`, never zero and never "within budget".
  - A known partial sum over the limit is `EXCEEDED`.
  - `assess_budget()` is retrospective: usage exactly at a limit is still within it.
  - `admit_next()` is a pure predicate that grants nothing. It denies another call
    once any wall-clock or token limit is reached, not only exceeded; a zero token
    limit admits no call.
  - `detect_repeated_failure()` matches an exact `(action digest, failure code)` pair.
  - A provider that never reports usage blocks a second call while a token budget
    is set (`USAGE_UNKNOWN`). This is the chosen fail-closed default.

<a id="a11c1-s1a--offline-task-inspection-candidate-independent-review-pending"></a>

## A11c.1 S1a — Offline task inspection (DONE/MERGED via PR #128)

```sh
llmtool task inspect --contract contract.json --expect expect.json \
  --events events.jsonl --observed-at 2026-10-12T12:00:10Z --json
```

This command reads three explicit files and returns a reproducible reported-state
snapshot and handoff **preview**. It does not observe live processes, run commands,
call providers, launch agents, fetch/change Git, change PRs or write a journal.
No config, credentials or stdin are discovered. Regular-file read access can update
OS atime. S1b later integrates Claude CLI review; S2 execution/repair needs its own
authority contract. A11b remains incomplete and F2 remains NOT AUTHORIZED.
The standalone helper remains outside llmtool; see
[tools/ai-loop/README.md](../ai-loop/README.md).

The original contract is the unchanged `task_verification.TaskContract` JSON.
Its existing canonical `digest` and `binding(run_id)` provide the expected task
binding; the CLI never adds lifecycle fields to TaskContract. Select `--expect`
independently of the event stream. A matching event cannot set its own expected
identity, root, run, source or epoch. Even tampering **both** inputs is not approval:
all bindings and supplied execution/review records remain caller claims.

Version-1 expectation JSON has **exactly** these fields:

| Field | Required value |
| --- | --- |
| `schema_version` | Integer 1 |
| `task` | Existing TaskBinding: task_id, run_id (32 lowercase hex), contract_version, contract_digest; equals the original contract binding |
| `source_sha`, `epoch` | Exact 40/64 lowercase hex SHA and positive integer epoch |
| `initial_state`, `initial_sequence` | `QUEUED`, integer 0 |
| `agents` | 1..32 AgentBinding records with DEVELOPER and REVIEWER participants |
| `authored_by` | Up to 32 pinned DEVELOPER AgentBindings covering all assigned developers; null/empty or missing coverage is INCOMPLETE, never assumed independent |
| `heartbeat_ttl_seconds` | Integer 1..86400; stale at observed time >= last timestamp + TTL, including terminal claims |
| `check_digest`, `artifact_digests` | SHA-256 of supplied check metadata and sorted unique artifact SHA-256 list (up to 32) |

AgentBinding fields are `agent_id`, `session_id`, `role` (DEVELOPER or REVIEWER),
`root`, `common_dir` and `branch`. IDs are bounded literal identifiers; roots are
literal absolute POSIX paths; common_dir ends in `/.git`. These paths are bindings,
not permission or filesystem observations. Developer branch cannot be `main` or
`automation/ai-loop`. A reviewer must have a distinct **agent ID AND Session ID**
from every pinned author/developer, including historical authors. Provider brand,
profile or OS UID is not consulted; a non-author instance of the same provider can
be structurally independent. Actual review/authorship is still NOT_VERIFIED.

Events are UTF-8 **JSONL**, one exact TaskEvent object per line, no blank records.
Fields: `schema_version`, `event_id`, `sequence`, `epoch`, `task`, `source_sha`,
`agent` (full AgentBinding), `kind` (STATE or RESUME), `state`, `prior_event_id`,
`prior_state`, `timestamp`, `provenance_digest` (SHA-256 or null), `evidence`
(existing EvidenceRecord array), `handoff` (HandoffRecord or null). Each binding
equals the independent pins. Missing provenance digest yields INCOMPLETE; a
non-null digest is only a claim, not authentic provenance. First event is sequence
1, QUEUED, STATE, with null prior fields. Later IDs are unique, sequence is
consecutive and prior fields must identify the immediately preceding event.

Allowed transitions: QUEUED → STARTING → RUNNING → WAITING_REVIEW → COMPLETED;
STARTING/RUNNING/WAITING_REVIEW may instead enter BLOCKED or FAILED.
FAILED/COMPLETED are terminal. BLOCKED resumes only with explicit next-sequence
RESUME → STARTING and a strictly later timestamp under fresh caller observation.
No automatic resume or health inference. Timestamps and `--observed-at` use only
`YYYY-MM-DDTHH:MM:SSZ`; no offset/fraction/default machine clock. Future timestamps
and rollback reject. Changed source/contract/epoch needs a separately pinned replay.

HandoffRecord fields: `task`, `source_sha`, `epoch`, `from_agent`, `to_agent`,
`check_digest`, `artifact_digests`, `prior_event_id`, `outstanding_criteria`.
On WAITING_REVIEW it binds the sending developer, assigned non-author reviewer,
preceding RUNNING event and exact pinned digests. Outstanding criteria must equal
the supplied-evidence verifier's unmet criteria. COMPLETED must come from the
target reviewer; REVIEW evidence is accepted only from that bound reviewer.
Evidence binds task/source; command digest and artifact references match the pins;
IDs cannot be counted twice. MODEL_CLAIM satisfies no criterion.

| Result / exit | Meaning |
| --- | --- |
| `SUCCESS` / 0 | Structure/replay and supplied required evidence metadata meet the pins; **not actual task success** |
| `INCOMPLETE` or `STALE` / 1 | Missing/failed/partial supplied required evidence, unknown authorship/provenance, missing handoff, empty replay or expired heartbeat |
| `INVALID` / 2 | Input/schema/binding/transition/clock failure; sanitized fixed reason code. Invalid CLI use also exits 2. |

Output separates `reported_state`, `structural_validation`, `evidence_structure`
and missing-evidence findings. `runtime_evidence_status` is **always NOT_VERIFIED**,
`authorized` is **always false**; `runtime_missing_evidence` names the still-missing
actual provenance/independent review attestation. Even a reported COMPLETED and
structural evidence PASS never certify actual tests, review, VERIFIED or DONE.
Preview has `triggered=false` and omits root/Session paths; hashes and ledger state
grant no actions. Read-only inspection does not author an authenticated ledger.

Inputs: contract retains existing 64 KiB/schema bounds; auxiliary files <=1 MiB,
events <=128, raw/canonical event <=16 KiB, evidence <=32/event and <=128 total,
agents/authors <=32, output <=1 MiB. Strict UTF-8/JSON, no coercions (including
bool-as-int), duplicate/unknown/missing keys, unsafe controls or credential text.
Input paths follow the existing literal POSIX spelling rules, reject traversal,
symlink ancestors/leaf, devices/FIFOs, multi-link or unstable regular files. Held
directory FDs, identity checks and bounded repeated-content reads detect drift;
`--expect`/`--events` inode aliases reject before event parsing. Unsupported
platforms fail closed. Same-UID checks are cooperative, not an OS sandbox.

See [Issue #120's accepted v1 product contract](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6123940052)
and the [workflow gates](WORKFLOW.md#s1a-offline-task-event-inspection-contract).
Company Round 1–3 evidence/privacy/accounting rules remain unchanged; no provider
or benchmark call and no new savings claim is part of S1a.

## A11b.4 P1 — Offline Patch Safety Preflight (no effects)

The parked P1 implementation and its exclusive tests are archived in Git;
see [the restoration record](#archived-parked-implementations).

## Archived parked implementations

Per [Issue #120's B/C cleanup](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6289392046),
this source tree removes 15 parked modules and their exclusive tests:

- F1 Git fixtures: `_git_add_private`, `_git_add_host`, `_git_supervisor`,
  `_git_containment`, `_git_containment_protocol`, `_git_containment_child`,
  `_git_process_identity`, `git_mutation`, and `patch_safety`.
- Dormant Codex pickup/audit: `codex_approval_audit`,
  `codex_approval_hook_adapter`, `codex_pickup_native`, `rollout_census`,
  `codex_handoff_transport`, and `task_handoff`, plus the dedicated
  `scripts/native_step0_census.py` wrapper.

The complete restoration point is commit
[`d41600ff98568770fb918fdf4a3309dd144a0dfe`](https://github.com/davidcatcat/davidcattoolsproject/tree/d41600ff98568770fb918fdf4a3309dd144a0dfe/tools/llmtool).
Restore the related modules, dedicated tests and shared test dependencies together
in a separately authorized task branch; historical reports remain in Git.
Restoring source does not authorize native pickup or F2 repository mutation.

The public `codex-cli` provider, read-only Git/task/review commands, active
OpenCode chain, and shared authorization policy/ledger remain. Their policy and
ledger tests use a small local notification fixture instead of the archived
Codex transport harness. Older DONE/merged and source-readiness statements below
record past evidence, not availability of the archived implementation.

## A11c.1 S1b Product A — Review preparation (fake-only candidate)

```bash
llmtool task review --contract ready.json --acceptance-contract final.json \
  --expect expect.json --events events.jsonl --json
```

S1b A validates the existing preparation TaskContract/S1a ledger and bounded
read-only local Git/worktree/import facts. **Real Claude is BLOCKED**: B's trusted
Host Session Attestation, independently approved contract pairing, Production
Grant Issuer/durable one-use protection and provider/data-route approval are not
implemented here. `REVIEW_AUTHORITY_UNAVAILABLE` lists those missing gates;
not-ready, stale or drifted inputs fail earlier. No CLI/JSON/environment fake,
grant or provider switch exists. This command loads no provider configuration,
launches no agent/provider, writes no artifact/journal and changes no Git refs.
It may run bounded, fixed read-only Git subprocesses. Use physical input paths
such as `/private/tmp` on Mac; symlink ancestors like `/tmp` are rejected.

The private Python test seam exercises one fake observer/grant/provider journey,
exact-SHA original source/diff packet, trusted-test pairing, strict independently
bound Security/Functional verdict and CHANGES_REQUIRED/BLOCKED handoff. Its
outputs are always `test_only: true`, `production_handoff: NOT_DELIVERED`,
`runtime_evidence_status: NOT_VERIFIED`, `authorized: false`, with unknown
usage/cost null. Fake APPROVE/exit0 is a review-test result, never COMPLETED,
actual execution verification, production authority or merge. Public CLI remains
non-PASS (exit1 for blocked/incomplete; exit2 for invalid input).

Pre-review admission requires structural SUCCESS **and** WAITING_REVIEW **and**
valid pinned handoff, plus actual identity/evidence/contract-pair/route/budget
checks. S1a exit0 can accompany RUNNING/BLOCKED/FAILED and is insufficient.
Historical terminal records do not renew heartbeat or live eligibility. Required
final Security/Functional REVIEW criteria are retained in a separately approved
immutable final contract; caller `--expect`, model replies and ledger strings
cannot approve the pair or authenticate the host.

Packet source/diff limit is 128 KiB, total 256 KiB, test/CI metadata 32 KiB,
JSON depth 16 and verdict 64 KiB. `REVIEW_PACKET_TOO_LARGE` gives sanitized
component/limit metadata and recommends splitting a scoped PR; no truncation
or removal of original evidence. Fixed host instructions treat all packet
source/diff/tests/CI/findings as untrusted DATA; JSON escapes role/delimiter
spoofing. Fake tests verify these construction/consumer boundaries, **not
universal LLM prompt-injection resistance**. Private paths/Session strings and
recognizable secrets are rejected; screening is not universal secret discovery
or export permission. A future approved B host must supply authentic original
execution/CI/Session provenance; local SHA/digest reads alone do not prove it.

One fake attempt, no retry/fallback/repair, <=120s call and <=180s foreground;
unknown-cost routes or required unenforceable hard spend caps fail closed.
No S2/F2, test execution, automatic source repair or Git/PR mutation/merge.
[WORKFLOW contract](WORKFLOW.md#s1b-foreground-review-contract-draft) and
[Product A/B handoff](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6127970737)
remain normative. B's contract checkpoint/implementation/native acceptance and
separately authorized actual Claude Mac smoke remain required for real automatic
handoff; this candidate does not deliver that production behavior.

### S1b B1 — Authority adapters (fake-first candidate)

Product A fake-only was accepted/merged via PR #131. B1 adds host-only typed
services, static Registration/RoutePolicy validation, ProductionReviewIssuer,
and a distinct provider layout in the existing ReceiptLedger. Public `task review`
still returns unavailable authority: no production Host settings/ledger/route
is installed and no actual provider/data export is authorized.

```sh
llmtool review-authority check --host /private/tmp/example/host.json --json
```

This checks an explicit existing physical file's structure/safety only; it writes
nothing and never issues/consumes a grant. Exit0/structural SUCCESS keeps
`authorized=false`, `published_pin_validation=NOT_VERIFIED`, zero provider calls.
Missing/unsafe/invalid input gives a sanitized error. There is no setup or fake
CLI/env/JSON upgrade. Production pins originate independently from the Integrator.

Provider receipts commit consume before an effect, enforce one reviewed object
per route plus an atomic UTC-day cap, and keep crash/timeout UNKNOWN charged.
D1 files/schema/decisions remain unchanged; cross-layout opens are rejected.
Tests use private temporary fake Host/ledger/transport fixtures with isolated
shebangs and owned-child cleanup. These are fake-native evidence, not production
Host authentication or real Claude smoke. Usage/cost remain null when unknown.

Review instructions now include the complete strict verdict schema/echo values.
Privacy permits narrowly defined literal home-path placeholders, retains actual
private path/Session/auth/secret denial, and never transforms exact source/diff
bytes. Some complete originals will still be BLOCKED by privacy; this is intentional.
Unexpected post-consume exceptions retain one attempted call and UNKNOWN.
Independent product Security/Functional review and T1/T2/T3 release/route gates
remain required; no F2/S2, auto repair/merge or production enablement is delivered.
See [B1 contract](WORKFLOW.md#b1-authority-adapters-and-fake-first-acceptance).


## Issue #135 — Codex tool-event audit collector (not an activation)

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Track 3 native command-hook adapter candidate (not installed)

Archived implementation; see [the restoration record](#archived-parked-implementations).

### S1b fake-only handoff preparation (Issue #120)

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Codex Pickup B1: disabled native boundary

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Native Delta A1: source readiness, no Native Grant

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Native Composition A1 — source/fake only

Archived implementation; see [the restoration record](#archived-parked-implementations).

### CR-K2-B HOST_ROLLOUT — Source/Fake only

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Local Review Bundle (M2 evidence handoff slice)

```sh
llmtool task review-bundle --contract /absolute/private/contract.json \
  --expect /absolute/private/expect.json --events /absolute/private/events.jsonl \
  --base FULL_BASE_SHA --evidence /absolute/private/evidence-index.json \
  --output /absolute/private/new-bundle.json --json
```

This foreground command prepares review DATA in the current developer worktree.
It reuses TaskContract, independent Expectations, event replay and the hardened
read-only Git collector. It requires current UTC freshness, structural SUCCESS,
WAITING_REVIEW and a valid non-author handoff. The observed own-root import/venv,
registered locked branch, common directory, clean tracked state and exact HEAD
must match the expected developer. A present CODEX_THREAD_ID must match too;
environment and caller identities still are not Host attestation.

The full base must be an ancestor of the expected committed HEAD. Every changed
path is checked against authorized/forbidden scope, including added and deleted
files. The complete manifest contains base/source blob OIDs and file modes, not
raw source or diffs; it is **not dependency closure**. A reviewer independently
reads the immutable referenced commits/blobs and adjacent dependencies as needed.
This does not relax the separate privacy-screened raw-source export route.

All four input files and referenced artifacts must be explicit physical owner
0600 regular single-link files in owner0700 directories outside protected project
roots/workspace parents. Symlinks, special files, credential/config locations,
aliases and changed files are refused. On macOS use the physical `/private/tmp`
spelling rather than its `/tmp` symlink. No directory is created implicitly.
The output parent must already exist privately; output is a **new** O_EXCL,
no-follow 0600 file. Existing files are never overwritten. On a late error a
partial/full private artifact can remain for diagnosis; it is not a success
receipt and the command never automatically cleans or retries it.

Evidence index version1 has exactly `schema_version`, `source_sha`, `artifacts`.
Each artifact (maximum16) has exactly `artifact_id`, `kind`, `source_sha`, `path`,
`sha256`, `metadata`. `kind` is TEST, COMMAND, CAPTURE or CI. The first three use
metadata `{ "exit_code": 0, "argv_sha256": "FULL_SHA256" }`; CI uses
`{ "url": "https://github.com/davidcatcat/davidcattoolsproject/actions/runs/123",
"head_sha": "FULL_SOURCE_SHA", "conclusion": "SUCCESS" }`, with conclusion
SUCCESS, FAILURE, CANCELLED, PENDING or UNKNOWN. Digests must match actual original
UTF-8 bytes and the declared source must match independent expectations. Commands
and URLs are never executed/fetched. Artifact bodies/logs/model replies are not
copied into the bundle. Hash matching authenticates neither their origin nor their
reported exit/CI result: these stay **CALLER_REPORTED**, execution unverified.

Inputs/artifacts are individually limited to 1MiB; artifact reads together to 4MiB
(both stable-reader passes count), private bundle to 1MiB and public JSON to 16KiB.
JSON rejects duplicate keys, excessive depth, invalid UTF-8 and nonfinite numbers.
Git operations share the existing 32-read/30s/2MiB collector budget; overall
preparation uses a 60s deadline with checks between bounded local operations.
It spawns only owned fixed read-only Git children, never providers, tests, shell,
network requests or Agents. No new approval store, controller or daemon exists.

Exit0 and `status=PREPARED` mean **only that the bundle was prepared**. Public JSON
contains its SHA256, task/base/source and counts, without private root/Session/
artifact paths. `authorized=false`, `runtime_evidence_status=NOT_VERIFIED` always;
Git observations are LOCAL_GIT_FACTS. The private bundle includes input references,
complete manifest, artifacts and a bounded Security/Functional review-request
template. It is local/private, not an export grant or automatic GitHub post.
Errors return fixed BLOCKED codes/nonzero without private details. Losing stdout
cannot yield exit0. Independently verify the actual bundle and evidence before
review; a prepared bundle never means tests PASS or Review APPROVE.

This is one local evidence handoff slice, not the complete M2 autonomous cycle,
Agent wake-up, production review authority, Native/Company Live or F2/S2 execution.

### Cooperative foreground review request / return (original Reviewer only)

The separate trusted-host Worker entry is
`llmtool.review_authority.run_foreground_review(issuer, ledger, budget=budget)`
(with the finite Integrator-facing receipt wrapper
`llmtool.review_handoff.dispatch_review_handoff(issuer, ledger, budget=budget)`).
It uses the issuer's existing pinned context and actual `compose_host_services`
with the `llmtool-worker/claude-cli` role (`claude --print --output-format json
--no-session-persistence --safe-mode --tools 'WebSearch,WebFetch'` and `stdin`
EOF closure; only WebSearch and WebFetch are allowed, never interactive terminal
paste or manual `Enter`); legacy Reviewer routes are rejected
before consumption/spawn. There is no new CLI flag, default Host path, grant
file, ledger creation or enabled public factory. Only already authorized trusted
host code with permitted Host/route/runtime/auth/ledger pins may call it.

One atomic attempt per issuer includes composition failures and cancellation;
reusing another issuer cannot bypass the existing ledger's replay checks. The
ledger is borrowed and left open. The caller can inspect read-only
`issuer.foreground_services` and `issuer.pending_job`; retained ownership remains
reachable on UNKNOWN/SystemExit/BaseException, without retry or fallback. Keep
the issuer alive and preserve unresolved child/evidence handles. The original
<=180s absolute deadline includes composition, caps the actual Worker launcher,
and gates final acceptance; delayed handoff does not reset it. Existing <=120s
call, UTC windows and bounded cleanup still apply.

The offline E2E uses only temporary synthetic executable/Host/ledger fixtures,
including actual Production composition, dual decisions and owned cleanup.
Production components' `test_only=false` is not proof of a live model call or
Host readiness. Results remain `WORKER_DATA_ONLY`, `authorized=false`,
`STOP_NO_EXECUTION_AUTHORITY`, runtime `NOT_VERIFIED` and handoff `NOT_DELIVERED`.
They do not replace independent formal Source approval or authorize repairs/Git.
Live still needs a separate one-call route/data/model/cost/window grant, permitted
physical executable and dedicated Auth HOME, Host/ledger provisioning and the
outstanding pre-Live reviews. No original Reviewer wake-up, reduced manual relay,
useful live adoption or full M2 completion is claimed. See the
[Worker Source/Fake contract](WORKFLOW.md#foreground-worker-sourcefake-contract-issue-120).

```sh
llmtool task review-request --bundle ABS_PRIVATE_BUNDLE --bundle-sha256 FULL_SHA256 \
  --expect ABS_PRIVATE_EXPECT --output ABS_NEW_REQUEST --json
llmtool task review-wait --request ABS_REQUEST --request-sha256 FULL_SHA256 \
  --receipt EXACT_RECEIPT_PATH --verdict EXACT_VERDICT_PATH --timeout 600 \
  --output ABS_NEW_REPORT --json
```

This task-scoped local DATA protocol binds the original Codex author Session
`01a12f8c-6a46-71c2-a7b3-3e6211ec2e9c` in its fixed Codex root and original
Claude Independent Reviewer `081de22e-d514-4288-9d1f-48f488979271` in canonical.
These fixed pins cannot be replaced by a response's claimed role or a new model
process. Caller and environment identities remain cooperative claims, not Host
authentication. The original Reviewer must separately attest actual receipt/use
and source review in its signed-in GitHub review. Do not synthesize its real
receipt/verdict or execute unreviewed candidate code in its Session.

`review-request` revalidates the existing bundle digest, independent EXPECT,
referenced TaskContract/events/evidence, current UTC readiness and non-author
handoff. It binds exact task/run/epoch/source/base, private bundle/EXPECT references,
complete changed paths, criterion IDs and evidence reference IDs. Its unpredictable
request ID and expiry (at most 15 minutes, never later than readiness expiry) are
DATA, not an authorization grant. The new private request contains static writer
instructions and complete bound receipt/verdict JSON examples with explicit
placeholders for actual request/receipt digests and observed timestamps.

Public `REQUEST_PREPARED` is **not** delivered, read, review-started or approved.
There is no queue/send/resume/provider/network/GitHub action or Agent launch.
The original idle Reviewer wake-up interface remains unavailable. Arrange an
actually active Reviewer slot separately; a GitHub post alone is not that evidence.
No Owner-written JSON or copied verdict is required.

For each recipient artifact, the original Reviewer uses its own ordinary trusted
read/evidence-writing tools, not the candidate package. Follow this publication
order: create the exact named DATA file exclusively, ensure mode 0600, finish and
flush/close it, compute SHA256 of those actual bytes, then exclusively publish and
close `DATA_PATH.ready`. Each ready marker has exactly:

```json
{"schema_version":1,"artifact_sha256":"ACTUAL_DATA_SHA256"}
```

Do not overwrite, rename over or clean any existing artifact/marker. Parent must
already be physical, owner0700 and outside protected workspaces. DATA/markers must
be owner0600 regular nonsymlink single-link files. Ready markers are completion
indicators, not identity proof or locks. Missing marker means uncommitted DATA is
not read; a committed malformed/missing/changed/aliased artifact is BLOCKED.

Recipient receipt schema (all fields required, no extras; substitute the complete
bindings/examples from the request):

```json
{
  "schema_version":1,"kind":"REVIEW_RECEIPT",
  "request_id":"BOUND_REQUEST_ID","request_sha256":"ACTUAL_REQUEST_SHA256",
  "task":{"task_id":"BOUND_TASK","run_id":"BOUND_RUN",
          "contract_version":1,"contract_digest":"BOUND_CONTRACT_SHA256"},
  "epoch":1,"source_sha":"BOUND_SOURCE_SHA","base_sha":"BOUND_BASE_SHA",
  "bundle_sha256":"BOUND_BUNDLE_SHA256",
  "reviewer":{"agent_id":"BOUND_REVIEWER_AGENT","session_id":"BOUND_REVIEWER_SESSION",
              "role":"REVIEWER","root":"BOUND_REVIEWER_ROOT",
              "common_dir":"BOUND_COMMON_DIR","branch":"BOUND_REVIEWER_BRANCH"},
  "received_at":"ACTUAL_SECOND_RESOLUTION_UTC_Z","bundle_read":true
}
```

Result uses the same bound common fields and a **different** kind:

```json
{
  "schema_version":1,"kind":"REVIEW_VERDICT",
  "request_id":"BOUND_REQUEST_ID","request_sha256":"ACTUAL_REQUEST_SHA256",
  "task":{"task_id":"BOUND_TASK","run_id":"BOUND_RUN",
          "contract_version":1,"contract_digest":"BOUND_CONTRACT_SHA256"},
  "epoch":1,"source_sha":"BOUND_SOURCE_SHA","base_sha":"BOUND_BASE_SHA",
  "bundle_sha256":"BOUND_BUNDLE_SHA256",
  "reviewer":{"agent_id":"BOUND_REVIEWER_AGENT","session_id":"BOUND_REVIEWER_SESSION",
              "role":"REVIEWER","root":"BOUND_REVIEWER_ROOT",
              "common_dir":"BOUND_COMMON_DIR","branch":"BOUND_REVIEWER_BRANCH"},
  "receipt_sha256":"ACTUAL_RECEIPT_SHA256","reviewed_sha":"BOUND_SOURCE_SHA",
  "completed_at":"ACTUAL_SECOND_RESOLUTION_UTC_Z",
  "security":"APPROVE","functional":"APPROVE","findings":[],
  "criterion_evidence":[{"criterion_id":"BOUND_CRITERION","evidence_refs":["source-manifest"]}]
}
```

Separate Security/Functional values are APPROVE, CHANGES_REQUIRED or BLOCKED.
Each criterion must occur exactly once; evidence references must come from the
request. Findings (at most 32) have exactly finding_id, category (Security or
Functional), severity (SEV-0 through SEV-4), priority (P0 through P3), release_blocker
(boolean), summary/reproducer (each 1..1024 UTF-8 bytes, screened), path (selected
changed path), criterion_id and evidence_refs. IDs are unique. A mandatory blocker
or SEV-0/1/2 finding prevents that category's APPROVE. These are structured
**reports**, never a new meaning of model text as approval. Receipt time must be
after request creation; completion cannot precede receipt or follow UTC expiry.

`review-wait` only checks the exact receipt/verdict names fixed inside the one
private request directory, at most twice a second, with no Git/network/provider
operations or background worker. Timeout is integer 0..600 (default 300), including
input validation, parsing and output. Timeout 0 performs one non-sleeping status
pass, with its local I/O bounded to 1 second; it never starts a waiting loop.
Request/recipient DATA are capped at 64KiB, marker at 1KiB, private report at128KiB,
public JSON at16KiB. Existing bundle/input/artifact read limits remain unchanged.
Symlinks, FIFOs/devices, aliases, oversized/invalid Unicode/deep/duplicate-key JSON,
wrong identities/digests/paths, changed inodes, expiry and unsafe metadata fail
closed. Request/inputs/committed artifacts are rechecked before output. Output is
new/private/O_EXCL; failure evidence is preserved, never automatically cleaned.

Final observations keep REQUEST_PREPARED, RECEIPT_REPORTED and VERDICT_REPORTED
distinct. Only both correctly bound committed reports before deadline give exit0
`BOUND_REVIEW_REPORT`. Missing result/receipt is WAIT_TIMEOUT/nonzero (with any
partial receipt observation retained); verdict without receipt is BLOCKED.
Interrupt is nonzero. Actual elapsed and digests are recorded, not invented model
turn/token counters. Runtime/identity remain NOT_VERIFIED/CALLER_REPORTED and
authorized=false even when both reported verdicts APPROVE. A BLOCKED verdict can
be successfully *received* without becoming review PASS. Late/unknown reports are
preserved, never silently reused, resent, retried or transformed into TaskEvents.

Synthetic file roundtrips test the protocol only. Real acceptance additionally
requires the original Reviewer's own receipt/result plus formal nonauthor review
on the actual PR HEAD, and Developer receiving them through llmtool. If no active
Reviewer is available or the bounded wait times out, STOP with real-use incomplete;
do not call a new model or extend indefinitely. This is not auto-wake, a daemon,
Git polling, #153 automation, another task ledger/approval store or complete M2.


### Safe consumption of reported review decisions

`review-wait` exposes the exact validated `security` and `functional` decision
enums in both public JSON and its corresponding private report. It derives them
only after the existing full request/receipt/verdict, source/task/run/epoch,
identity, digest, expiry and file checks. Public output still excludes findings,
private paths, Session IDs and model prose. The private `review_report` is retained.

| Security / Functional | `review_disposition` |
| --- | --- |
| Either BLOCKED | BLOCKED (including BLOCKED + CHANGES_REQUIRED) |
| Otherwise either CHANGES_REQUIRED | CHANGES_REQUIRED |
| APPROVE / APPROVE | DUAL_APPROVAL_REPORTED |
| No complete valid bound report | null decisions/disposition, never approval |

All projections include `execution_disposition=STOP_NO_EXECUTION_AUTHORITY`.
Existing transport compatibility is unchanged: a correctly bound BLOCKED or
CHANGES_REQUIRED report still returns `BOUND_REVIEW_REPORT`/exit 0. Timeout and
errors remain nonzero. Error responses cannot retain earlier valid decisions.
`authorized=false`, CALLER_REPORTED identity and NOT_VERIFIED runtime/formal
acceptance remain unchanged. Receipt, reported approval, independent formal
Source approval and execution authority are distinct. No caller flag, JSON
claim or reported decision authorizes repair, tests, Git, merge or a provider.

A caller must parse the JSON, check both decisions and independent expected
bindings, and stop without separately verified action authority. For example,
this bounded-data consumer never executes anything:

```python
def consume_review(report, expected):
    # expected comes from independent task/request pins and observed digests,
    # never from the received report itself. Parse strict bounded JSON first.
    fields = ("task", "epoch", "source_sha", "base_sha", "request_id",
              "request_sha256", "bundle_sha256", "receipt_sha256", "verdict_sha256")
    if (report.get("status") != "BOUND_REVIEW_REPORT" or
            any(report.get(k) != expected[k] for k in fields)):
        return "STOP_INVALID_OR_UNBOUND_REPORT"
    pair = (report.get("security"), report.get("functional"))
    if any(v not in ("APPROVE", "CHANGES_REQUIRED", "BLOCKED") for v in pair):
        return "STOP_INVALID_DECISION"
    disposition = ("BLOCKED" if "BLOCKED" in pair else
                   "CHANGES_REQUIRED" if "CHANGES_REQUIRED" in pair else
                   "DUAL_APPROVAL_REPORTED")
    if (report.get("review_disposition") != disposition or
            report.get("authorized") is not False or
            report.get("execution_disposition") != "STOP_NO_EXECUTION_AUTHORITY"):
        return "STOP_INVALID_REPORT"
    # No independently verified execution grant exists in this workflow.
    # Do not substitute exit 0, dual APPROVE, a caller flag or model text.
    return "STOP_NO_EXECUTION_AUTHORITY"
```

This adds usable decision consumption to the existing foreground artifact
protocol. It does not install automatic original-Session delivery or wake-up,
change Reviewer identity, enable a repair/action runner, or complete the M2 loop.
Original Claude must still actually receive/read the request and write its own
receipt/verdict. Arrange an active slot before generating short-lived data;
manual relay counts remain visible. Automatic dispatch and execution need their
own accepted interface/authority gates; no daemon, polling or alternate provider
is implied.
