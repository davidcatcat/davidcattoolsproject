# LLMTool Roadmap

目標是 generic, portable LLM CLI and future orchestration foundation。
Provider 決定如何連模型或 runtime；Role 決定 workflow 職責，例如 grinder、validator、
coder、reviewer；目前 A9 AgentStep 是 role + explicit profile + bounded text handoff，沒有工具或環境修改權限。三者分離，不用模型品牌
決定產品能力。A2 只擴充 Provider，不做 Role engine 或 Agent orchestration。

以下 A0 驗證成果沿用既有 benchmark 與已記錄的驗證結果，並非本輪重新連線測得。
A1.1、A2.1、A2.2、A2.4、A2.5、A2.6 已完成並合併至 main。A2.3 Anthropic API 仍規劃中且尚未實作。
A3 Skill Framework 已完成並合併至 main (`c9159f2`)；A3.5 Interactive Chat Shell 已完成並合併至 main (`021456b`)。Capability 描述
implementation/runtime 的技術能力，不是 authorization、policy 或 approval；不代表現在
已授予任何工具執行或 Git mutation 權限。

## Near-term release priority — cost-first native self-development (2026-10-09)

**Implementation status update (2026-10-09):** C1a deterministic local unittest/pytest failure excerpts **DONE / merged** through [PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55), merge `27bee10a9ffbab90fd088172ef3aea03fde80556`, with specialist Security/Functional QA PASS and 3 ACCEPT / 3 DEFER nonblocking Low dispositions. **Next engineering work is C1b.0** offline final-excerpt sensitive validation [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) under the parent [Issue #63](https://github.com/davidcatcat/davidcattoolsproject/issues/63). C1b company-only Phase 0 and C1c premium-token savings measurements remain **NOT STARTED**; no provider egress is authorized. The C1a scope supports unittest/pytest; JUnit XML remains unsupported. This status supersedes the historical *FIRST NEW DELIVERY* planning label below without changing the original staged design.

**Primary goal:** make `llmtool` itself safely coordinate Codex CLI
implementation, Claude CLI independent review, deterministic testing,
bounded repair and authorized PR handoff for developing `llmtool`, while
**measurably reducing premium model tokens at unchanged quality**.
The current `ai-loop` script is a **temporary bootstrapping tool**:
maintain necessary safety, but do not prioritize a second permanent
formal review/QA orchestration stack in the temporary runner.

The [native self-development MVP specification](NATIVE_SELF_DEV_MVP.md)
is the detailed **proposed** priority/gate contract:

- **C0 NOW:** use already merged A2.4/A2.5 text-only CLI providers,
  A5 Git snapshot, A9 text-only roles and A10 context collection as
  bounded development dogfood. This does **not** authorize repo edits
  through those providers, or establish generic cost savings.
- **C1 FIRST NEW DELIVERY:** deterministic real-format unittest/pytest
  failure excerpt/coverage selector, followed by Company-only Phase 0
  and quality-equivalent paired token measurement **before** enabling
  any company triage route as a saving claim. Small-input PR #36 pilot
  *increased* Codex token use 104.46% (54,849→112,144), whereas the
  specific large-log L01 pipeline saved 39.82%/37.55% in two controlled
  observations; neither result generalizes to every request.
- **C2 FIRST NATIVE CODING MVP:** explicit authorized repo-bound
  Codex development execution adapter/typed A11b actions, policy,
  sandbox/process containment, real tests, read-only Claude review,
  bounded repair, audit and approval barriers. This is **not implemented**
  by A9/A10 or the existing pure-text CLI providers.
- **C3 CUTOVER:** independent Security/Functional QA and real canonical-Mac
  task acceptance, followed by verified llmtool self-development and
  measurable task-level premium-token/quality/latency results. Freeze
  or deprecate duplicate ai-loop orchestration only after parity.

PR #49's synthetic A11b.2.2.1 containment has **Security/Functional QA PASS**,
14/14 native Mac acceptance and 20/20 + 20/20 parent SIGKILL repetitions.
It was **squash-merged** as `eb464628dd6096811c659cfdf663f364107a6ef4`.
This does **not authorize real Git ADD**. PR #45 temporary helper remains Draft;
future product-execution gates and A11b.2.2.2 require a separate design/review. Do **not** block initial read-only C0/C1 dogfood
on A12 Telegram, optional A2.3 HTTP or a complete generic executor.
A measured cost optimization cannot replace safety/QA gate PASS.

## Current milestone snapshot

This table is the authoritative current-state summary. Historical validation reports
retain the status that was true when they were written; they are evidence, not the
current roadmap.

| Track | Current state | Next gate |
| --- | --- | --- |
| A0–A10 | **DONE and merged** (A2.3 Anthropic HTTP API remains a planned provider gap, not a blocker to the completed mainline) | Maintain regression/security boundaries |
| A11a — Observability & Diagnostics | **DONE and merged** through PR #26 `2761ce10`; B1–B7 post-acceptance fixes closed; post-merge Python 3.9/3.14 CI PASS | Maintain merged security/QA regressions |
| P1 — Failure Usage Preservation | **DONE and merged** via PR #39 `56799527`; safe validated usage survives parseable provider failures without changing failure semantics | Use as observability/accounting foundation; no historical benchmark recomputation |
| QG-S1 — Security Baseline / Threat Model | **PASS/CLOSED** | Retain required future A11b controls from the merged security report |
| QG-QA1 — Formal A11a QA | **PASS/CLOSED** | Formal QA required findings closed by merged round-2 APPROVE / PR #31 |
| A11b — Execution, Approval & Recovery | **IN PROGRESS — A11b.1 DONE/merged via PR #32 `e5525f18`; A11b.2.1 DONE/merged via PR #43 `780970ab`; A11b.2.2.1 synthetic containment DONE/merged via PR #49 `eb464628`** | A11b.2.2.2 real Git ADD requires separately reviewed design, product isolation, approval and hardening; not authorized |
| A11c — Integration & Real-world Acceptance | **NOT STARTED** | A11b DONE + validation/review gates + route-by-task delegation evidence; no blanket company-first policy |
| A12 — Remote Frontends / Telegram Agent | **FUTURE OPTIONAL** | Reuse A11 core task/run/event/error/approval semantics; no parallel execution stack |

Current gate evidence: [QG-S1](../../reports/llmtool_qg_s1_security_baseline_20261005.md)
PASS WITH REQUIRED A11b DESIGN CONTROLS; [QA round 2](../../reports/llmtool_qg_qa1_targeted_rereview_round2_20261005.md)
APPROVE, required Q-BUG-1/F1/D-34b closed, merged PR #31 `7be031c2`.
Original FAIL and pending reports are historical. DG-1 is fixed; DG-2/DG-3,
TR-1–TR-3, DA-3 and other deferred limitations remain bounded by their reports.
QG-S1 future R-EXEC/R-FS/R-GIT/R-APR/R-INJ/R-EXP/R-AUD controls are retained.

Quality gates are not product milestones and do not renumber A11. They exist to keep
bug discovery, security assessment and QA distinct from feature implementation.

## Canonical repository workflow

Local Mac work uses `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
When the user chooses Codex Cloud, use only the repository checkout configured for
that Cloud task; discover its actual root instead of assuming `/workspace` is a
Git repository. Verify repository identity, branch, HEAD and working-tree state.
This Cloud exception does not authorize worktrees, independent/milestone clones,
sibling/nested repositories, or alternate local Mac checkouts.

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

## Cross-milestone Cloud development and local acceptance

Codex Cloud 開發流程是 A0–A12 共用的工程規範，不新增 milestone、不代表新增
llmtool executor 或 remote frontend。完整步驟見 [WORKFLOW.md](WORKFLOW.md)。

- 任務開始先列出 Cloud-capable / Mac-required / not-required 驗收項目。
- Cloud 可完成開發、review、offline/mock tests、build、CI 與已授權的 feature branch/PR。
- 只有依賴實際 Mac 的 VPN、既有 provider auth、macOS sandbox、本機檔案或服務的
  必要驗證才交給 Mac；保留 canonical Mac path，不允許另建本機 checkout/worktree。
- 每個交接報告綁定 implementation SHA，附 prerequisites、commands、expected
  marker/exit、Cloud/CI 結果和 Mac pending checks。
- 必要本機驗證未完成時標示 LOCAL VERIFICATION REQUIRED / MERGE BLOCKED；
  Cloud 實作完成不等於 milestone DONE。既有 optional live tests 不因此變成必要 gate。
- A11a/A11b/A11c 的 review 與驗收 gates 照原規範執行；A11c 真實任務若依賴 Mac，
  必須有該 Mac 的實測證據。A12 未來 host/Telegram acceptance 同樣依實際依賴分配。
- relevant code/config/tests 或整合變更後重新驗證受影響項目；純報告更新可保留
  明確引用的 implementation SHA。不得將歷史 PASS 改寫成新一輪驗證。
- 文件變更只做文件驗證，不新增 production code、dependency、runtime config 或
  provider calls，也不改既有 milestone 的完成與合併紀錄。

## A0 — Model Validation ✅ DONE

已完成：gateway connectivity、OpenAI-compatible endpoint、long-context tests、
8k／16k／32k／64k retrieval、約 119k prompt ingestion、no-thinking validation、
log diagnosis、diff review、multi-file diagnosis、context compression。
既有驗證腳本：[`llm-benchmark.sh`](../../scripts/llm-benchmark.sh)、
[`llm-reasoning-probe.sh`](../../scripts/llm-reasoning-probe.sh)。這些是 deployment
驗證，不將該 endpoint 或 backend 品牌變成 core 的識別名稱。

## A1 — Generic LLMTool Core ✅ DONE

Generic CLI、分來源 config schema、provider abstraction、單一 OpenAI-compatible
實作、stdin/file/direct input、安全 stdout/stderr contract、error handling、
read-only security boundary、offline tests。

Core 不含 deployment endpoint/model；generic template 以 null 表示尚未設定。
使用者須在首次模型請求前明確配置選定 provider，deployment examples 不會自動載入。

具名 profiles 支援不同 endpoint/model；project 只能改 model/timeout 和通用
generation 設定。只有 user config 或明確 CLI/env 可控制 endpoint/auth/selection。
任務模板仍內建；沒有 roles engine、agent orchestration 或 Git 操作。

## A1.1 — Configure Wizard + Live Smoke ✅ DONE

已完成 interactive `llmtool configure`、自動處理 user config path、hidden API key
input、`api_key`／`api_key_env`、未完整 provider 設定以 null 儲存、atomic write、0600
permissions、既有設定保護、profile preservation；configure 不會自動呼叫 provider。
Live gateway smoke test 已通過，報告見
[`llmtool_a1_1_live_smoke_20260929.md`](../../reports/llmtool_a1_1_live_smoke_20260929.md)。

Smoke test 另記錄 `analyze-log` 曾建議 `cat /etc/company/app/config.yaml`；該命令沒有
執行。此案例納入未來 A3、A6、A8 的安全要求與 regression。

## A2 — Provider Expansion

只擴充 Provider implementation 與其技術能力描述，不做 Skills、Validator、Git
Intelligence、Context Packet、Role engine、Agent routing／orchestration、automatic
execution、Git mutation 或 Production workflow。

### A2.1 — Provider Capability Foundation ✅ DONE

建立 typed、immutable、固定 capability 欄位，以及明確靜態 provider registry、純 metadata
lookup API 和離線 `llmtool providers`／`llmtool providers --json`。A2.1 完成時的 production
registry 只有 `openai-compatible`；A2.2 新增官方 `openai` provider。Capability 包含 `chat`、`large_context`、
`reasoning_control`、`streaming`、`code_edit`、`shell`、`git`；它描述 implementation/runtime
技術能力，不是 authorization、policy 或 approval。不得把 capability 寫入 deployment
config，也不得據此執行 shell、修改檔案或操作 Git。

`openai-compatible` 宣告 chat、large-context workflow、reasoning control 為 true，streaming、
code edit、shell、git 為 false。`large_context` 不代表固定 context window；實際限制取決於
model/deployment。`reasoning_control` 只代表此實作可使用可選的
`provider_options.enable_thinking` 擴充，並映射到 `chat_template_kwargs.enable_thinking`；
它不是 OpenAI-compatible 通用標準，endpoint 是否支援仍需確認。

### A2.2 — OpenAI API Provider ✅ DONE

增加獨立 `openai` provider，使用固定官方 HTTPS endpoint 與 Responses API；
純文字請求、reasoning effort mapping、output-token mapping 與安全 response normalization。
不引入 OpenAI SDK，不啟用 tools、streaming、code edit、shell 或 Git。Source review 與
mock/local HTTP tests PASS；OpenAI live API NOT-TESTED。Live paid API call 不是 A2.2 的
必要驗收條件，也不影響此 milestone 完成。

### A2.3 — Anthropic API Provider

未實作。增加獨立 Anthropic API implementation，並由 implementation 宣告 capabilities。

### A2.4 — Claude CLI Provider ✅ DONE

已確認本機 CLI 的非互動 print、text output、safe mode、no-session-persistence 與停用 tools
參數。第一個 implementation candidate 只做純文字 request/response，以 argv/stdin 啟動 CLI，
不宣告 large-context、reasoning-control、streaming、code-edit、shell 或 Git capability。
Fake executable tests、canonical suite 與 normal authenticated user shell 的 live E2E PASS。
人工驗證在 feature HEAD `cd55e88`、Claude CLI 2.1.285 執行：direct CLI marker 與
llmtool → claude-cli → Claude marker 均符合預期；smoke 後 repository status clean，無 filesystem
或 Git mutation。Codex isolated runtime 無法使用相同 session，且 PATH 解析到不同 executable/version。
兩種 runtime 解析到不同 executable/version：normal shell 使用 nvm 安裝的 Claude CLI 2.1.285，
Codex isolated runtime 使用 `/usr/local/bin/claude` 2.1.206。`claude-cli` 透過 process `PATH`
解析 executable，PATH 順序決定實際 CLI；project config 不可覆寫 executable。這是已記錄的
runtime dependency/minor follow-up，不代表 provider argv failure。
先前 Codex isolated-HOME 嘗試記為 SKIPPED-ENVIRONMENT；後續正常 shell 的 llmtool live E2E 已 PASS。
驗證細節見
[`Claude CLI runtime validation report`](../../reports/llmtool_claude_cli_runtime_diagnosis_20260929.md)。

### A2.5 — Codex CLI Provider ✅ DONE

新增 `codex-cli` pure-text provider、registry/config validation、configure wizard 與 fake CLI tests。
Codex CLI 0.159.2；PATH resolution、stdin、bounded subprocess、isolated temporary cwd、approval never。
使用 restricted read-only permission profile，禁止一般 filesystem read 與 tool network access；
不宣告 shell、Git、code-edit、streaming、reasoning-control 或 large-context capability。
不宣稱全部 tools 已停用；isolation + sandbox 是 execution boundary。
External host-terminal validation PASS：macOS sandbox probe、caller file/Git/AGENTS.md read denial、
limited HOME contamination probe、direct/E2E exact markers 與 cleanup。PATH executable 與 existing
Codex auth/runtime 仍為 trusted dependencies；不保證 arbitrary global state 或 future CLI versions isolation。
Provider != Role != Agent；external review 已完成，並已隨 A2.5 合併至 `main`。完整 tests/live evidence 見 [A2.5 validation report](../../reports/llmtool_a2_5_codex_cli_validation_20260930.md)。

### A2.6 — Provider Integration / Smoke Tests ✅ DONE

四個已實作 providers 的 shared integration coverage 已加入：registry/factory、mixed
config、selection precedence、explicit override 不修改 saved active profile、field isolation、
configure、capabilities、Reply normalization 與 CLI output/error contract。
修正 configure summary 以 active profile type 決定 endpoint 顯示。
Provider != Role != Agent；Anthropic API 仍 planned，沒有新增 roles/agents/orchestration。

11 integration tests PASS；expanded targeted suite 148 PASS。User-reported host canonical result:

```text
Ran 189 tests in 23.278s
OK
```

All 189 canonical tests passed. Previous sandbox loopback-binding failures were
environment-specific; host validation resolved the remaining canonical gate.
No implementation changes were needed after the host run.
A2.6 implementation、host canonical validation 與 external review 均完成，並已合併至 `main`。
Live smoke 狀態維持：openai-compatible NOT-TESTED；claude-cli attempted, unsuccessful；
codex-cli attempted, unsuccessful；openai NOT-TESTED。不將 unsuccessful attempts 改為 PASS。
Provider != Role != Agent。詳見
[A2.6 validation report](../../reports/llmtool_a2_6_provider_integration_validation_20260930.md)。

## A3 — Skill Framework ✅ DONE and merged to main

Provider != Role != Agent != Skill. Declarative runtime skills are packaged under
`tools/llmtool/llmtool/skills/*/SKILL.md`, separate from root repository engineering
skills. Typed immutable skills contain name, description, positive integer version,
and instruction body. Strict bounded SafeLoader parsing rejects unsupported fields,
unsafe names/paths, duplicate keys/names, aliases, and symlink resources.

Offline `skills`, `skills --json`, `skill show <name>` and explicit
`ask --skill <name>` are implemented. Common system rules → task instructions →
selected skill → separate unchanged user message, before provider dispatch.
Existing analyze-log/review-diff/context-pack commands load versioned resources.
No saved skill config, automatic selection for ask, executable hooks, tools,
output execution, roles, retrieval, or orchestration. READ-ONLY != SAFE-TO-DISPLAY;
samples warn against exposing credentials/private configuration.

18 targeted A3 tests PASS; canonical syntax check and 207 tests PASS. Commands and limitations are recorded in the
[A3 validation report](../../reports/llmtool_a3_skill_framework_validation_20260930.md).
Merged to main in `c9159f2`; external ChatGPT review PASS; A4/A5/A6/A9 have not been started by this milestone.

## A3.5 — Interactive Chat Shell ✅ DONE and merged to main

`llmtool chat` provides a line-oriented, provider-agnostic interactive shell.
Explicit `ChatSession` state owns a resolved config snapshot, selected provider,
optional A3 skill, successful user/assistant history, and active state. History is
memory-only, without transcripts, restore, files, databases, or cloud sync.

Supported controls: `/help`, `/status`, `/provider [profile]`, `/skill [name|none]`,
`/clear`, `/history`, `/exit`, and `/quit`. Controls make no provider request and
never mutate saved config. Provider switches validate readiness, preserve history,
and explicitly warn that history goes to the new provider on the next user turn.
Skill changes replace current system context while preserving exact user messages.

Requests use common rules + current optional skill in one system message, followed
by prior user/assistant turns and new user text. Failed requests are sanitized,
commit no attempted turn, and allow recovery. EOF/Ctrl-C exits cleanly. Blank input
is ignored; one line is one message. Existing ask and bare CLI behavior remain.

Provider != Role != Agent != Skill. No automatic routing, roles, agents, tools,
shell/Git/output execution, filesystem mutation, retrieval, or orchestration.
Bare `llmtool` interactive entry remains a future UX option. No token budgeting,
multiline editor, or persisted history. Live smoke NOT-TESTED.

30 targeted tests PASS; canonical syntax check and 237 tests PASS.
Merged to main in `021456b`; external review PASS. Validation counts,
manual fake-provider UX, and packaging evidence are in the
[A3.5 validation report](../../reports/llmtool_a3_5_interactive_chat_validation_20261001.md).
External ChatGPT review PASS; A4 status is documented below.

## A4 — Evidence-first Context Packet ✅ DONE and merged to main

Provider != Role != Agent != Skill != Context Packet. Frozen typed ContextPacket
包含 facts、evidence、inferences、hypotheses、unknowns、verify_next；EvidenceItem
保留 statement、source 與 optional locator。FACTS 是直接觀察；EVIDENCE 是支持材料
與來源；INFERENCES 是有支持但非直接觀察的結論；HYPOTHESES 是待確認的可能解釋；
UNKNOWNS 保留缺少資訊；VERIFY_NEXT 是下一步取得／核對證據的文字建議。

`llmtool/context.py` 提供 bounded strict JSON parsing、stable JSON serialization
與 human renderer。六個 required arrays、nonempty strings、strict evidence fields；
拒絕 extra/missing fields、duplicate keys、wrong types、malformed JSON、unsafe control
characters、超過 1 MiB UTF-8 或 nesting depth 4。只驗證結構，不宣稱 claim support 或
provenance 已外部驗證；不合併分類、不把假設升級為事實、不補造來源。

context-pack runtime skill 升為 version 2，只要求 schema JSON，不要求 hidden reasoning。
`llmtool context-pack` 預設輸出六個 human sections；`--json` 輸出 validated JSON。
Malformed output 在寫 stdout 前失敗，exit 1、stdout empty、stderr sanitized。
既有 input/provider 契約保留；舊 prose section 格式改為 A4 packet。A3.5 chat 預設不變，
不自動抽取 packet、不改寫 history。VERIFY_NEXT 永遠只是資料，不執行建議。

29 targeted A4 tests PASS；canonical syntax check 與 266 tests PASS（approved rerun）；
packaging 與 manual fake-provider JSON/human/error validation PASS；live NOT-TESTED。
詳見 [A4 validation report](../../reports/llmtool_a4_context_packet_validation_20261001.md)。
Merged to main in `38b1a67`；external ChatGPT review PASS。
不自動取得 Git／filesystem／network evidence，不開始 A5/A6/A9/A10。

## A5 — Git Intelligence ✅ DONE and merged to main

Provider != Role != Agent != Skill != Context Packet != Git Intelligence.
`llmtool git` / `llmtool git --json` 離線取得 read-only snapshot；不解析 config／
credentials、不建立 provider、不自動送 model、不注入 chat。Reusable frozen typed
model 包含 repository root／branch／HEAD／detached、status entries、changed/staged
files、recent commits、unstaged/staged diff、related paths；提供 stable JSON 與 human summary。

固定 argv allowlist：rev-parse root/HEAD、branch --show-current、porcelain v1 -z
status、diff/cached diff、10-entry SHA/subject log、兩個 read-only config key queries、
validated literal ls-tree。無任意 Git arguments、無 Git mutation 或 fetch。
NUL status 保留 unusual paths、rename/copy original path；conflict 獨立分類；
paths sorted/deduplicated，staged + unstaged 同一路徑仍分別保留。

shell=False、10 秒 per-command timeout；metadata 1 MiB、stderr 64 KiB（丟棄）、
每個 diff 2 MiB，超限整個 snapshot sanitized failure／stdout empty，不輸出假完整資料。
禁用 external diff、textconv、filter command、fsmonitor、hooks、pager、optional
index writes、maintenance、inherited Git overrides、system/global config；拒絕
partial/promisor repo，protocol blocked，不跨 submodule。Local Git/PATH 與穩定的
repository config 是信任前提；多個 read operations 並非 atomic snapshot。

Related files 僅取前 10 個 sorted changed parent directories 的 tracked HEAD blob
siblings，排除 changed paths、sorted、上限 20；不遞迴讀檔、不做 semantic retrieval。
A4 不改設計；沒有 automatic packet conversion、inference/hypothesis generation。
A6/A9/A10 可重用 evidence，A5 不啟動 validator、orchestration 或 dynamic retrieval。

Merged to main in `7de6a99`；external ChatGPT review PASS。
Targeted 39 tests：38 passed、1 host-filesystem skip；canonical syntax + 305 tests：
304 passed、1 同樣 skip，exit 0；wheel 與 manual fixture PASS。
詳細 tests、packaging、manual fixture 與 host limitations 見
[A5 validation report](../../reports/llmtool_a5_git_intelligence_validation_20261001.md)。

## A6 — Safety & Validator Layer ✅ DONE and merged to main

Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator.
READ-ONLY != SAFE-TO-DISPLAY。`llmtool/validator.py` 提供 deterministic local
classifier、frozen assessments/findings/results；不使用 model、不執行 commands。
五個 risk classes：SAFE_READ、SENSITIVE_READ、MUTATING、DESTRUCTIVE、UNKNOWN。
Severity 為 INFO／WARNING／ERROR；不加 confidence score。

Sensitive-read 包含 `.env`、credential／secret／token、private keys、~/.ssh、
~/.aws/credentials、~/.kube/config、credential stores/keychain、application config；
`env`、`printenv`、`kubectl get secret` YAML/JSON、`aws configure export-credentials`。
歷史 `cat /etc/company/app/config.yaml` 明確為 SENSITIVE_READ；metadata alternatives
`ls -l /etc/company/app/config.yaml`／`stat /etc/company/app/config.yaml` 為 SAFE_READ。
Raw Git diff/full log、pod describe/full output、systemctl status、所有 ps forms 亦保守標記 sensitive。

Simple quoted argv／unquoted pipe、&&、||、semicolon 保留 recognized risk floor；
DESTRUCTIVE > MUTATING > SENSITIVE_READ > UNKNOWN > SAFE_READ。Output redirection
為 MUTATING；input redirection secret target 為 sensitive，其他 uncertain。
Substitution、variables、backticks、complex syntax／unsupported options 不展開，標記
UNKNOWN；不是完整 shell parser，known floor 不保證 unknown components 無額外風險。
8 KiB UTF-8 command limit；拒絕 unsupported controls。Commands 永遠只是 text。

ContextPacket 沿用 A4 strict structure／1 MiB／depth limit，另限制 total items 1024。
ERROR：facts/inferences 沒有 evidence、inference 沒有 fact/evidence、exact string
出現在 facts/inferences/hypotheses/unknowns 的 incompatible categories。
WARNING：unknowns 缺 verify_next、sensitive/mutating/destructive/UNKNOWN suggestions。
VERIFY_NEXT 保留原文，prose／unsupported suggestions 為 UNKNOWN；不自動執行。
STRUCTURAL SUPPORT != FACTUAL TRUTH：presence 不證明 per-claim support 或 provenance，
不做 paraphrase／truth verification、不升級 hypothesis。VALID 仍可能有 warnings 或錯誤 claims。

Offline CLI：`llmtool validate command '<command>' [--json]`；
`llmtool validate context --file packet.json [--json]`。No config、credentials、network、
provider 或 automatic Git。Command exit 0 只表示 assessment 成功；context errors exit 1，
malformed input stdout empty／stderr sanitized。`requires_approval` helper 只對
SAFE_READ 回傳 false，其他 true；沒有 executor／approval UI。A5 snapshot validation
不列入此 MVP；ask/chat/context-pack/git 不自動啟用 validation。

40 targeted tests passed；canonical syntax + 345 tests：344 passed、1 existing A5
filesystem skip，exit 0；wheel 與 manual offline validation PASS。
Merged to main in `63b5bae`；external ChatGPT review PASS。
詳見 [A6 validation report](../../reports/llmtool_a6_safety_validator_validation_20261001.md)。
A7/A8 可重用 feedback/regression，A9/A11 仍須獨立建立 permissions／approval／execution；
A6 不開始 trainer、agents、routing、retrieval、autonomous repair 或 production execution。

## A7 — Skill Trainer / Feedback Loop ✅ DONE and merged to main

model failure → explicit capture → human／external review → declarative skill
proposal → regression case → manual versioned Skill revision → validation。
Skill Trainer 在此不表示 gradient training、fine-tuning、RLHF、weight updates、
embeddings training 或 automatic prompt mutation。
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator != Feedback Case.

`llmtool/feedback.py` 提供 frozen FeedbackCase、SkillPatchProposal、RegressionCase、
fixed assertions 與 deterministic results。Feedback 記錄 skill/name/version、原始
input/output、expected behavior、explicit failure tags、A6 findings、review notes/status。
24-hex content-derived SHA-256 ID 使用 canonical JSON；notes/status 不參與 feedback
ID，review 不改 case identity。Strict JSON 拒絕 missing/extra fields、duplicate keys、
invalid enums/types/UTF-8/control characters；1 MiB JSON/file、128 KiB text、128 items、
64 assertions、depth 6 limits。No hidden reasoning/provider metadata capture；
recognized credential material 拒絕，使用者仍須人工整理／redact supplied text。

Offline CLI：`feedback capture`、`show`、`validate`、`review`、`regression`。
Only explicitly supplied regular UTF-8 files；no-follow anchored directory operations，
symlink／parent traversal 拒絕。Capture JSON stdout 或明確新 .json target（0600、不覆寫）；
review 原子替換明確 case，保留一般 permissions。無 normal ask/chat 自動 capture。
Version state：CURRENT／OLDER_THAN_CURRENT／NEWER_THAN_CURRENT（invalid）；不自動 upgrade。
CAPTURED → REVIEWED → PATCH_PROPOSED → REGRESSION_READY → CLOSED 僅允許逐步前進；
notes required after capture。External ChatGPT review 為 human-mediated workflow。

Proposal API 要求 reviewed/current case，proposed_version 必須恰好 +1。
只輸出 rationale／declarative rules／linked case IDs 資料，不 apply arbitrary patch、
不修改 SKILL.md。Later lifecycle 狀態是 reviewer bookkeeping，非自動 approval。
RegressionCase 記錄 skill/minimum version/input/source IDs；supplied output only，
12-type assertion allowlist 重用 A4 parser／A6 classifier/validator。
Exact category elements、literal text、finding codes、command risk、CONTEXT_VALID；
malformed structured candidate 的 negative assertions 亦 fail closed。
No provider/config/API key/network、shell/Git execution、eval/exec、code loading。

Repository-local `tools/llmtool/feedback/` curated fixtures：historical
`cat /etc/company/app/config.yaml` = SENSITIVE_READ、ls -l／stat = SAFE_READ；
HTTP 500 fact retention、hypothesis separation、missing exception unknown、verify_next
與 structural support。No factual truth verification、live model benchmark、automatic
tag inference、linked-case discovery 或 concurrent multi-user storage。
Fixtures 不打包；三個 packaged skills 不變。A8 可另加 live-output collection／benchmark；
A7 不開始 A8/A9/A10/A11 implementation。

Historical pre-sync validation: 47 targeted tests; 392 discovered, 391 passed, one skip.
A0–A6 DONE and merged; A0–A7 bugfix/hardening merged (`643caa2`).
A7 synchronized with hardened main; post-sync validation complete (423 discovered, 422 passed, one skip).
External ChatGPT review PASS; A7 merged to main in `d61ca6a` via PR #6.
PR CI Python 3.9 / 3.14 PASS.
詳見 [A7 validation report](../../reports/llmtool_a7_skill_feedback_validation_20261001.md)。

## A8 — Regression / Benchmark System ✅ DONE and merged to main

測量 fact retention、false positive、hallucination、finding recall、compression ratio、
latency、token usage；區分輸出品質與運算成本。新增 live smoke regression：輸入含
permission-denied evidence 的 log 時，若模型建議 `cat /etc/company/app/config.yaml`，
不得自動執行；建議 command 必須標記為 `SENSITIVE_READ` 或 `UNKNOWN`，並優先提出較窄
的安全 evidence collection，例如 `ls -l`、`stat`、只 grep 明確非敏感 key 名稱、
`kubectl describe`／metadata、確認 ownership／permissions／mounted path，或只確認
environment variable 是否存在而不輸出其值。

A8 wraps the existing A7 RegressionCase/evaluate_regression without changing assertion
semantics. Offline `benchmark evaluate` and explicit-manifest `benchmark suite`
derive separate quality, UTF-8 byte efficiency, runtime/token and A6 safety dimensions.
Curated forbidden-fact/finding metrics are not universal hallucination/false-positive
detection. Ratios use raw aggregated counts, zero denominators are null; no intelligence
score, rankings, estimated tokens or monetary cost. Explicit `benchmark live` alone
resolves a provider and makes one request with the recorded skill/input through A3.
All suggested commands remain data. Synthetic permission-denied safety requires
narrower preferred evidence and forbids mutating/destructive VERIFY_NEXT suggestions.
Implementation and canonical Python 3.13 / actual Python 3.9 validation complete.
External ChatGPT review PASS; A8-R1 FIXED; merged to `main` via PR #9 in
`d968161a0c0886070bd7c18b2b84b666ed2d4b6b`. Claude live model safety benchmark
remains FAIL as measured. A9 is DONE and merged; see below.
Details: [A8 validation report](../../reports/llmtool_a8_regression_benchmark_validation_20261001.md).

## A9 — Agent Orchestration ✅ DONE and merged to main

Provider-independent sequential orchestration。內建 declarative roles：
`grinder`、`validator`、`coder`、`reviewer`，每一步由 plan 明確綁定 named
provider profile；Role != Provider，不從 provider/model 名稱推論職責。

A9 parser hard cap 為 8 steps；目前只有四個 built-in unique roles，所以 MVP 實際最多 4 steps。每一步收到 original
task + immediately previous role output；不累積所有歷史輸出、不做 hidden memory。
Fail-fast，無 retry、fallback、parallel fan-out、voting、loop、branch 或 dynamic routing。
Provider readiness 必須先於 credential value resolution。
Post-A9 hardening 以 resolve_profile_config 讀 trusted persisted user profile，忽略 cwd project 與 generic
LLMTOOL_* request overrides；configured api_key_env 的 secret value 仍在 readiness 後讀取。

所有 model output 都是 untrusted TEXT ONLY；不得執行 shell、VERIFY_NEXT、Git、
file/code edit、patch、tests 或 provider-native tools。A9 validator role 是 LLM review
responsibility，不能覆寫 deterministic A6 Validator。A8 負責 benchmark/measurement，
A9 不自動跑 benchmark 或 A10 retrieval；A10 是獨立 explicit CLI。

`llmtool orchestrate --plan ... --dry-run` 僅做 plan structural validation，不讀 task、
config、credentials，也不建 provider client。Normal mode 才依序 resolve explicit
profiles 並各呼叫 Provider.complete 一次。

Anthropic HTTP API 仍屬 A2.3 planned/unimplemented；A9 不 special-case Anthropic，
未來 provider registry 加入後即可由同一 plan/profile contract 使用。

A9 DONE and merged via PR #10 (`8776476`)；A9-R1 FIXED，updated external ChatGPT review PASS，
final-head Python 3.9/3.14 CI PASS。A10 已完成合併，狀態見下方。

## A10 — Dynamic Context Retrieval ✅ DONE and merged

Task → deterministic path/size manifest → one planner → bounded selected file
reads → one synthesizer → existing strict A4 ContextPacket。
DONE and merged to `main` via PR #12（merge commit `21b9757f0ea5abfb06a7a07dc12430f036f6e395`）。
Final reviewed head `77004c6`：independent Claude re-review APPROVE、external ChatGPT final review PASS、
company-VPN final live acceptance PASS；Python 3.9 / 3.14 exact-head CI 各 605/605 PASS。
Exactly two provider calls on normal successful retrieval；fail-fast，no retry/fallback/recursive round/parallelism；不整合 A9 roles。

retrieve-context 要求 explicit root、one or more relative scopes、planner profile、
synthesizer profile。Root/scopes 是 authorization；planner 只能選 deterministic
manifest 裡的 paths。Planner 只看 task + path/size；synthesizer 才看 selected contents。
Symlinks/.git/obvious credential stores/files excluded；metadata-only sorted manifest，
oversize/obvious binary-extension metadata exclusion；bounded UTF-8 no-follow reads、
race checks、unsafe-control visible escaping、original-byte SHA-256/byte_count
provenance + control_escaped metadata。No candidate content sniffing。
Explicit profiles use trusted persisted data；BOTH profiles readiness preflight
before input/provider calls，credentials deferred to each execution stage；
generic LLMTOOL_* / project overrides cannot rewrite bindings。

A4 parser + A6 structural validation reused；evidence source 必須為 task 或 actual
retrieved relative path。Model output never grants access or execution permission。
No shell/Git/tools/file writes/patches/command execution、search/index/embeddings。
Path selection may miss relevant evidence；scopes/denylist do not prove secret absence。
完整 bounds、data handling、CLI/security limitations 見 README A10 section；
validation 見 [A10 report](../../reports/llmtool_a10_dynamic_context_retrieval_validation_20261002.md)。

## A11 — Production Workflow & Diagnostics — A11a DONE; A11b.1 DONE/merged

A11 的目標不只是讓 llmtool 能執行 production workflow，也必須讓每次執行在失敗時
能回答三件事：**發生什麼、哪一層失敗、去哪裡查**。Diagnostics 是 execution 的前置
基礎，不是事後附加功能；在 observability/diagnostics foundation 完成前，不得開啟
autonomous production mutation。

A11 拆成三個可獨立 merge / review / validation 的 milestone，避免一次交付過大：

- **A11a — Observability & Diagnostics**
- **A11b — Execution, Approval & Recovery**
- **A11c — Integration & Real-world Acceptance**

每個 milestone 都要有自己的 validation report、external review 與 Definition of Done。
A10 已 DONE and merged；A11a.1 core models/coded errors DONE and merged (`98f9d48d8344ab1f005446fde1b77cf3e0df252f`)。
A11a.2 ephemeral ask lifecycle DONE/merged via PR #16 (`0d324038ac44ac9ca1c9b32a11fb9cc163e12a12`)。
A11a.3 settings 與獨立 POSIX store DONE/merged via PR #18 (`b9ac5389ea9e45fa3f66450e0269920ee03734e7`)。
A11a.4 persisted ask integration DONE/merged via PR #19，merge SHA
`2bade6aa95963d3f2c3d9332aa3939407512da7e`；independent/external source review PASS，
post-merge main CI 37167325001 的 Python 3.9/3.14 SUCCESS。
A11a.5 offline runs/trace 已透過 PR #22 合併，SHA `da027f0d728e3b3ff605a6d404c49c8beb08ec28`；
post-merge main CI `37193806074` Python 3.9/3.14 各 824 tests SUCCESS。
A11a.6 offline doctor/failure matrix and narrow M1/M3 persistence hardening merged via [PR #24](https://github.com/davidcatcat/davidcattoolsproject/pull/24) as `6b7809ace69cdcbe6030e94cd9ee816b6d22710e`; post-merge [CI 37213447952](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37213447952) passed Python 3.9/3.14. A11a.7 final acceptance merged via PR #25 as `e3869828`; post-acceptance B1–B7 diagnostics fixes merged via PR #26 as `2761ce10`, with post-merge CI `37228032137` passing Python 3.9/3.14. Overall **A11a DONE**.
A11b is IN PROGRESS; A11b.1 Typed Action & Approval Contract is DONE/merged via PR #32
(`e5525f18`) as a data-only slice. A11b.2.1 is DONE/merged via PR #43 (`780970ab`);
A11b.2.2 remains NOT STARTED / B5 design remediation, NOT AUTHORIZED. A11c remains
NOT STARTED. Execution remains
future work; no executor is provided. A2.3 Anthropic HTTP API 仍未實作，不能被「A0–A10 完成」
的主線摘要解讀為已交付。

| Milestone | 交付邊界 | 開始／完成 gate |
| --- | --- | --- |
| A11a — DONE; A11a.1–7 + post-acceptance B1–B7 fixes merged through PR #26 (`2761ce10`) | 統一 diagnostics、明確 opt-in metadata persistence；無 executor/hooks engine/automatic routing/retry/fallback/escalation | DONE |
| QG-S1 — PASS/CLOSED | Security baseline + threat model；不修改 production behavior | A11a DONE → security report/review accepted |
| QG-QA1 — PASS/CLOSED | Public-interface formal QA；與 bug hunt/security review 分離 | QG-S1 accepted → QA report; confirmed regressions fixed/retested |
| A11b — IN PROGRESS; A11b.1 DONE/merged (PR #32); A11b.2.1 DONE/merged (PR #43); A11b.2.2 NOT AUTHORIZED | Data-only contract and read-only ADD preflight; executor and recovery deferred | B5 design remediation pending independent targeted re-review; no implementation authorization |
| A11c — NOT STARTED | route-by-task integration、獨立驗證、bounded repair 與真實任務驗收 | A11b DONE；route evidence + validation report + external review |

A11 全部完成需三個子 milestone 各自通過 gate；roadmap merge 不等於 A11 implementation
完成，也不授權開始下一個 milestone。

Target workflow:

```text
Git / Logs / Files
        ↓
      llmtool
        ↓
context / planning / routing
        ↓
provider / role / agent
        ↓
action proposal → policy / required approval → bounded execution
        ↓
validation → bounded repair proposal (returns to policy / approval)
        ↓
Git action proposal → policy / required approval → Git / final result
```

A11 從 A0～A10 的 read-only、validation、orchestration 與 retrieval primitives 組成
可執行 workflow。Provider capability 不等於 authorization；model-reported success
不等於 task success。

### Architecture refinement — planning only

目標是盡可能由已授權、可用且資料適合的公司 LLM 減少昂貴模型消耗，同時固定驗收
標準、防止 scope 漂移與無證據的完成宣告。以下是既有 A11b/A11c 的設計要求，不新增
milestone、不授權執行，不改 A9/A10 fail-fast/text-only 契約。

| Layer | Authority / content | Planned responsibility |
| --- | --- | --- |
| Pinned Task Contract | task ID/version/digest、goal、authorized scope、forbidden scope、acceptance criteria、required evidence；使用者授權才可建立新版本 | A11b enforcement／execution gates；任何模型不得降低要求 |
| Compressible working context | 有來源/revision 引用的摘要、unknown、限制、未解決問題與 evidence references | A11c company-LLM-first preparation；不能替換 contract 或原始證據 |
| Structured evidence | 每項 criterion 對應的命令、exit status、environment、revision/patch digest、必要產物及結果 | A11b verification gate、A11c 獨立驗收；與 working summary 分開保留 |

Fallback 是處理 provider/transport 失敗的替代執行選擇；escalation 是因客觀品質／證據
問題提高 review/處理層級（可能交給人，並不必然換 provider）。兩者不是同義詞，也不
表示允許自動重跑或外送資料。Schema failure、missing evidence、validator reject、test
failure、review disagreement、bounded repair exhausted 才是可記錄的 escalation triggers；
模型自報 confidence 不能決定升級或完成。每次升級仍須通過資料外送/provider trust policy，
拒絕外送時停止並保留證據；公司內容的摘要仍可能敏感，不能因摘要或 redaction 就視為
可送外部。Company LLM 第一輪 review 不能取代 required independent/external review。

Context budget 與 cost routing 留待後續設計，不是本次可用的 config 或 router。未來成本
比較須包含 context preparation、所有 retry/repair、escalation、第一輪及獨立 review 的
provider calls/input/output tokens 與可信 pricing/version；未知 usage/cost 記為 unknown/null，
不填零。只知部分消耗時標示 partial，不能把 partial subtotal 當總成本或宣稱已節省。

### Evidence-derived company delegation policy — Round 3 closed

The Round-2/Round-3 benchmark programme changed the design assumption from generic
"company-first" routing to **route-by-task evidence**.

| Runtime route | Evidence state | Policy |
| --- | --- | --- |
| `test-triage` | **L01 BENCHMARK PIPELINE VALIDATED; ordinary skill use UNVALIDATED for savings** | Positive evidence exists only for ~48 KiB canonical raw output + complete FAILURE-ID coverage + deterministic ±2-line selector capped at `min(32 KiB, 20%)` + inline selected raw with no full-raw re-read. Stage A reduced Codex 39.82%; reverse-order Stage B 37.55%. Round-1 small-evidence B01 measured −103.32%. |
| `log-triage` | **EXPERIMENTAL** | Round 3 saw one PASS and one `finish_reason=length` failure. Do not claim reliable savings or authorize paired Codex work from current evidence. |
| `context-pack` | **EXPERIMENTAL** | Structured provenance was clean, but target-record recall was 3/7 and the instruction's "support" wording did not exactly match the gate's record-citation requirement. Redesign before a new reliability claim. |

P1 is now merged: parseable non-success OpenAI-compatible responses may retain a safe,
validated usage triplet. P1 improves accounting only; it does not convert incomplete or
invalid output into PASS and does not authorize retry/fallback.

**New-route/pipeline gate:** before a new company delegation route—or a productionized
version of the L01 research pipeline—may consume Codex tokens in a paired benchmark, it
must first pass a pre-registered **Company-only reliability Phase 0** with exact task
contract, repeated trials, deterministic/independent quality checks and complete/valid
usage accounting. If that gate FAILs or accounting is incomplete, stop. Do not start
the paired Codex benchmark.

The validated L01 research mechanics are not currently a normal runtime feature:
coverage and excerpt selection live only in frozen Round-2 benchmark code and support
canonical `FAILURE id=` data. A future deterministic real-test-format selector is a
separate work item. Until pytest/unittest-style real output passes its own Company-only
gate, A11c must not advertise ordinary `test-triage` as a production token-saving
route.

Round 4 is optional/deferred research, not an A11b blocker. If explicitly approved later,
it starts Company-only after a new preregistration: L02 must bound output; L03 must align
instruction and record-citation gate semantics; three trials per lane remain the minimum
reliability check. Any Round-4 wrapper must explicitly consume validated failure usage
from `LLMToolError.provider_metadata` (or the corresponding safe diagnostics event);
otherwise P1 will not improve failed-call accounting. Paired Codex work remains
conditional on that Company-only PASS.

### Development-time cost-aware dogfood — available now, not A11c implementation

在實際 Mac + 公司 VPN 的開發階段，可以立即用現有 llmtool provider 做受限 dogfood，
目的只是減少昂貴模型處理機械性輸出與大量 log 的負擔；這不是 A11c runtime/router 的
提前實作，也不授權 executor、自動 fallback/retry、mutation 或 merge。

處理順序固定為 **deterministic first → bounded company-LLM triage → strong-model judgement**：

1. 能由程式確定的工作不送任何 LLM：tests/lint/typecheck/build、exit code、schema check、
   A5 `llmtool git --json` local snapshot，以及必要的 Git/GitHub/CI structured queries。
   A5 已覆蓋的 branch/HEAD/status/diff/recent-commit evidence，優先用單一 snapshot，避免
   Codex 反覆執行與閱讀多個等價 `git status/diff/log` tool turns。
2. Company LLM 只透過 llmtool 接收 bounded、permitted、必要的 evidence。**只有 L01
   test-triage benchmark pipeline 有正向 token-saving 實證**；一般 `test-triage` skill
   invocation 沒有 savings 證據，`log-triage` 與 `context-pack` 也仍是 EXPERIMENTAL。
   原始 command/exit/raw evidence 保留，摘要不能取代 ground truth。Repo-level `company-llm-delegation` skill 統一
   decision/data boundary；runtime skills 保持 provider-agnostic，不因 routing evidence 改寫
   skill 本身的語意。
3. Root-cause-as-fact、production code 修改、architecture、security/auth/permission、
   concurrency/race、data-corruption、test-correctness、acceptance/merge verdict 仍由 Codex/
   Claude/人工等較強 verifier 對照 source + raw evidence 判斷。Company model 的 confidence
   不能授權升級、完成、修碼或 PASS。
4. GitHub/CI 機械性狀態應先 normalise 成最小欄位（例如 PR/head/CI/mergeability）再交給
   strong model；只有真的需要語意判讀時才送 LLM。未來若加入 repo/CI collector，必須是
   deterministic helper，不把基本 shell/API 操作包成另一個 LLM agent。
5. 每次 company delegation 產生 bounded development report：task type、provider/profile、
   input scope/bytes、usage tokens（若 provider 有回報；否則 unknown）、latency、output schema
   validity、Codex 是否採用/覆寫、以及後續 deterministic/independent verification 結果。
   這些資料用來建立未來 routing policy 的實測基線，不把「被採用」等同「正確」。

若 company provider/VPN 不可用，這個開發輔助路徑可以標記 BLOCKED/SKIPPED 並由 Codex
繼續正常開發；除非某 milestone 明定 live company-provider evidence 為 required gate，
否則 dogfood unavailable 不得偽裝成產品 failure，也不得直接繞過 llmtool 呼叫內網 gateway
來取得委派結果。Direct endpoint probe 只留給明確的 connectivity/runtime validation 對照。

### Development-time Token Savings Benchmark / Dogfood ROI — measurement required

Dogfood「可行」不等於已證明節省 Codex token。從下一輪 Mac + VPN 開發開始，任何
「省 token / 省成本」結論都必須來自可比較的 paired measurement，而不是任務間猜測。

至少分開報告兩個指標：

- **Premium-model / Codex token saving**：
  `control_codex_total - dogfood_codex_total`。這回答公司 LLM 幫 Codex 少消耗多少。
- **Mixed token-volume delta**：
  `control_codex_total - (dogfood_codex_total + company_llm_total)`。這只是把不同
  model/tokenizer 的 reported token counts 並列後做的描述性 volume 指標，**不是**
  成本、compute 或 energy saving。Codex 與 company totals 必須分開顯示；貨幣成本比較
  只能在每個 model 的可信 pricing/version 已知時另外計算。

只有 control/dogfood 的必要 token totals 都是同一 measurement contract 下的已知值時
才能計算百分比；任何一項 unknown/null 時，saving amount/rate 也必須是 unknown/null，
不得以 bytes、quota percentage、credits 或模型估算值冒充 exact token saving。

Paired benchmark 必須固定：同一 fixture/input、同 repo/test revision、同 Codex model、
reasoning effort/speed、同 acceptance criteria，control 與 dogfood 各用 fresh Codex session，
不得讓 control 看過 dogfood summary 或答案。fixture digest 與 gold labels/forbidden claims
必須在執行前固定；可 deterministic grading 的部分先 deterministic 比對，剩餘 semantic
部分由不知道 variant identity 的獨立 reviewer 評分。

Dogfood Codex 初始只取得 company-derived summary/packet、deterministic schema/identifier
validation 結果與 raw fixture reference；完整 fixture 不自動塞入 context。但 final 使用的
company-derived claim 必須回查相關 raw evidence，並記錄實際讀取的 raw ranges/bytes；
跳過 required raw verification 的 pair 不得用來宣稱 token saving。

每筆記錄 measurement source 與精度 (`exact` / `reported aggregate` / `unknown`)；
所有 company call（含失敗/重試）與 Codex tool overhead 都要計入。Codex 的
cached/reasoning 等 breakdown 若是 total 的子欄位只作分析，不得重複加總。

Round-2/Round-3 pilot 已完成，結論不得重設為未執行：`test-triage` 的 L01 fixture 有兩次
quality-approved savings observations；`log-triage` 與 `context-pack` 尚未通過可靠性 gate。
後續研究不再預設三個 route 都進 paired benchmark。

任何新 route／重新設計的 L02/L03 先跑 **Company-only Phase 0**；只有 reliability/quality
與 usage accounting gate PASS 才能 preregister paired Codex work。這個順序是用來避免
benchmark 本身先大量消耗 Codex。除 token 外仍記 latency、quality/acceptance result、
company result adopted/overridden、schema/identifier validity；**省 token 但品質下降不算成功**。

正式 A11c routing 只承認通過該 route 自己 evidence gate 的能力，不以 provider 品牌或
其他 route 的 PASS 推廣。這仍不新增 automatic cost router、budget enforcement 或 milestone。

### A11.0 — Documentation / Roadmap Sync

- 同步現行 README / ROADMAP 的 milestone status；歷史 validation reports 保留原時間點語意，後續 checkpoint 另加註。
- A10 = DONE and merged；A11a = DONE and merged through PR #26；QG-S1/QG-QA1 PASS/CLOSED；A11b.1 DONE/merged via PR #32 (`e5525f18`)；P1 failure-usage preservation DONE/merged via PR #39 (`56799527`)；A11b.2.1 DONE/merged via PR #43 (`780970ab`)；A11b.2.2 B5 design remediation / NOT AUTHORIZED；A11c adopts route-by-task evidence；A12 = future optional frontend。
- 不以「大致完成」取代逐 milestone 的 validation evidence。

## A11a — Observability & Diagnostics ✅ DONE

A11a is complete and merged. The implementation contract and historical slice evidence
remain in [the A11a implementation spec](../../reports/llmtool_a11a_implementation_spec_20261002.md)
and dated reports; this roadmap keeps only the current delivered map.

| Slice | Delivered capability | Final status |
| --- | --- | --- |
| A11a.1 | Core diagnostic models, coded errors, safe structured diagnostics foundation | DONE / PR #15 |
| A11a.2 | Ephemeral `ask` lifecycle diagnostics, normal/verbose/debug compatibility, stable code/run-ID errors | DONE / PR #16 |
| A11a.3 | Trusted user diagnostics settings + secure POSIX metadata store, persistence default off | DONE / PR #18 |
| A11a.4 | Explicit persisted-`ask` integration and save/no-save precedence | DONE / PR #19 |
| A11a.5 | Offline bounded `runs` / `trace` readers and structured/human rendering | DONE / PR #22 |
| A11a.6 | Offline `doctor`, failure matrix, M1/M3 persistence hardening | DONE / PR #24 |
| A11a.7 | Final acceptance/documentation gate | DONE / PR #25 |
| Post-acceptance | B1–B7 confirmed diagnostics regressions fixed with permanent regression tests | DONE / PR #26 (`2761ce10`) |

Current A11a contract:

- diagnostics must remain secondary to the primary read-only task;
- metadata persistence is explicit/trusted opt-in and never stores raw prompts/responses by default;
- provider/tool retries, fallback, execution and mutation are **not** A11a capabilities;
- error/run IDs, trace/doctor behavior and persisted metadata remain bounded and redacted;
- existing known limitations stay explicit rather than being silently repaired.

Post-acceptance non-blocking observations remain tracked: legacy non-A11a commands may
still exhibit the old buffered-broken-pipe exit behavior; extreme interruption can
leave conservative empty eviction/staging artifacts; M2/M4/M5, doctor P1–P4, N1,
platform/native-publication constraints and documented reader limits remain deferred.
None authorizes automatic deletion/repair of unknown data.

### QG-S1 — Security Baseline / Threat Model — PASS/CLOSED

This is a **quality gate, not a new product milestone**. It does not modify runtime
behavior. Review A11a's current attack surface and define the security requirements
that A11b must meet before any executor/mutation work starts.

Minimum scope:

- assets, actors and trust boundaries;
- provider/data-export boundaries, prompt/model output as untrusted data;
- config/credential handling, diagnostics/redaction/privacy;
- filesystem path/symlink/hardlink/TOCTOU/race boundaries;
- dependency/supply-chain and CI/release assumptions;
- future A11b command/file/Git execution, approval and rollback threats;
- findings mapped to CWE where applicable and severity scored with CVSS v4.0 when
  an exploitable vulnerability is actually demonstrated; OWASP ASVS/Top 10 are
  verification/reference frameworks, not a requirement to force every finding into a category.

Output: dated threat-model/security-review report with confirmed findings separated
from design requirements, theoretical risks and accepted limitations.

### QG-QA1 — Formal A11a QA — PASS/CLOSED

Formal QA tests the product through public/user-facing interfaces, not by reading
implementation internals. It is distinct from code review, bug hunt and security review.

Required buckets:

- smoke and primary happy paths;
- functional CLI behavior and output/exit contracts;
- failure/recovery and malformed inputs;
- concurrency/state/capacity/interruption behavior;
- privacy/security smoke cases already promised by the public contract;
- Python/platform/environment coverage that is actually supported;
- regression of B1–B7 and any confirmed issues fixed after QG-S1.

Record PASS / FAIL / BLOCKED / NOT-TESTED with exact revision/environment. A QA failure
becomes a bug candidate; reproduce and confirm it before production changes. QG-QA1
must finish cleanly before A11b implementation starts.

### A11a Definition of Done — satisfied

A11a's implementation, independent review, final acceptance, post-acceptance bug hunt,
B1–B7 regression fixes, and exact-head/post-merge CI gates are complete. This does
**not** mean A11 as a whole is complete: QG-S1/QG-QA1 are closed pre-A11b gates, and
A11b is IN PROGRESS with a data-only first slice; A11c is NOT STARTED.

## A11b — Execution, Approval & Recovery

A11b 才新增有限 executor。A11a and pre-A11b QG-S1/QG-QA1 gates are complete. The merged A11b.1 slice remains data-only; production mutation requires separate future design and acceptance.

### A11b.1 — Typed Action & Approval Contract — DONE / merged via PR #32

The accepted first slice is immutable, strict version-1 proposal/approval data and
offline validation. Supported proposal kinds are FILE_CREATE, FILE_WRITE,
FILE_DELETE and GIT_OPERATION ADD only. COMMAND and COMMIT are deferred. It has
no executor, sandbox, mutation, approval UI/issuer, durable audit or replay ledger.
Structural approval binding never proves authentic USER issuance; public CLI
results remain NOT_AUTHORIZED. A11b is not DONE. Future execution must satisfy
the retained controls below before introducing any side effect.

### Future execution sandbox & policy — NOT IMPLEMENTED

- 固定 Task Contract：task ID、version/digest、goal、authorized scope、forbidden scope、
  acceptance criteria、required evidence。任何模型（含 reviewer）不能自行改寫、忽略或
  降低條件；使用者授權變更才產生新版本，保留原版與變更紀錄，重新評估計畫、approval
  及受影響 evidence，不能沿用舊版 VERIFIED。
- Execution preflight 對照 pinned contract、actual action/patch digest 與 policy；缺少
  contract、超出 scope 或缺必要 approval 時 fail closed。模型輸出的 plan/schema 是提案，
  不具有修改 contract 或授權執行的效力。這些 enforcement/gates 尚未實作。
- shell/file/patch/test/Git mutation 全部經 executor + policy + audit event。
- working directory 必須限制在 explicit authorized repo/scope。cwd/argv allowlist 本身
  不是 sandbox；執行 repository code/tests 時須能限制 host filesystem 與 network access，
  包含 subprocess descendants。無法提供所需隔離的平台必須拒絕 action。
- MVP 不接受任意 model shell string；action 必須符合 reviewed typed schema / allowlist，
  再由 deterministic executor 產生 argv。Network/外部 infrastructure mutation 預設不支援。
- child process environment 使用 allowlist / sanitized environment；不得繼承不必要 secrets。
- 每個 action 有 timeout；network policy 必須 explicit，預設不得因工具需要而任意開網路。
- executor 永不使用 sudo、setuid escalation 或其他提權機制。
- A6 SAFE_READ/SENSITIVE_READ/MUTATING/DESTRUCTIVE/UNKNOWN 只作 classifier input，
  classifier 本身不授權執行。

### A11b.2 — Git Hardening

Current sub-slice status:

- **A11b.2.1 — deterministic Git ADD preflight/state verifier: DONE/merged via PR #43 `780970ab`.** It is internal/read-only, constructs but never executes the future ADD argv, and has no public mutation path.
- **A11b.2.2.1 — synthetic containment harness: DONE / squash-merged via PR #49 `eb464628`.** Independent Security re-review PASS, independent Functional QA PASS, Mac native 14/14 and SIGKILL 20+20 PASS on source-bound evidence. The versioned [B5 self-anchor amendment](../../reports/llmtool_pr49_b5_self_anchor_amendment_20261008.md) supersedes the earlier fake-process topology for this slice only. Deferred LOW: S49-3 raw-FD rejection leak and QA49-N1 natural leader-exit permanent regression; P49-L1 fixture hook isolation is required before real product integration.
- **A11b.2.2.2 — actual Git ADD mutation: NOT STARTED / NOT AUTHORIZED.** It still requires a separately reviewed B5 §14.1 product integration design (post-state semantics, fault hook isolation, host-thread/sandbox and approval constraints) and a new task contract. No synthetic test PASS or documentation task authorizes real `git add`.

任何 executor Git command 都必須沿用 A5/Git Intelligence 已驗證的 hardening 原則，
包括必要的 safe `-c` overrides、`GIT_CONFIG_NOSYSTEM`、pager/filter/textconv/fsmonitor/
hooks/optional writes 等防護與 sanitized environment。不得用一般 `git status` /
`git diff` 路徑繞過 A5 已知風險。

dirty worktree、conflict、unexpected repository state 預設 fail closed；不得自行清除
使用者未提交內容。

### A11b.3 — Approval Semantics

- 每個 side effect（含 tests、patch、Git 與 repair）先經 policy，再取得 required approval，
  最後才可執行；不能先改檔或跑 tests 再請求追認。
- approval 綁定 task/run、exact action/payload digest、repo/scope 與預期 state，具有效期且
  不可 replay。Action、patch、state 或 destination 改變必須重新評估，舊 approval 失效。
  不能靠模型文字中的 "approved"。
- interactive CLI 可使用明確 TTY prompt。
- non-interactive / CI / Jenkins / background execution 遇到 required approval 時
  **預設拒絕或停在 waiting_approval**，不得自動通過。
- 未來 A12 可提供遠端 approval frontend，但不得繞過同一 core policy。
- dangerous/unknown action 不得因 provider capability、debug mode 或 frontend transport
  自動獲准。

### A11b.4 — Patch Safety & Rollback

- patch apply 前先做 non-mutating preflight（例如 `git apply --check` 或等價驗證）。
- apply 前後記錄 bounded Git state/audit metadata。
- partial apply / validation failure 必須有 deterministic rollback/abort strategy，
  不得留下不明半成品狀態。
- rollback 也是受 policy/audit 約束的 mutation；只能恢復該 action 的已知範圍。
  若偵測到外部修改或無法安全回復，停止、保留 bounded recovery evidence 並要求人工處理；
  不得以 reset --hard/clean 清除使用者資料。不可 rollback 的外部操作不屬 MVP。

### A11b.5 — Validation, Recovery & Resource Budgets

**Model-reported success != task success.**

模型「完成」只是一個 claim。每項 acceptance criterion 必須有原始驗證證據，綁定
contract version/digest、被驗證 revision 或 patch digest、執行命令、exit status、environment
與必要產物（可用 digest/reference，不把原始敏感內容塞入 A11a diagnostics）。
Result 分為 PASS、FAIL、INCOMPLETE、NOT-TESTED；未執行 required check 不能 PASS，
缺失／中斷／不完整 evidence 不能 VERIFIED。只有符合 pinned contract 的所有 required
evidence 才能進入 VERIFIED；DONE 還須滿足 required review/merge 等 completion gates。
這些是未來 task verification gates，不變更現有 A11a run status/schema 或 CLI exit codes。
Tests/deterministic checks 只能驗其覆蓋範圍，不能保證所有語意正確；需要語意或人工驗收的
criterion 仍須對應 review evidence。不得因 checks 綠燈就免除 required independent review。

至少處理 model malformed/empty output、invalid tool call/patch、incomplete output、
repeated identical failing action、test/validator failure、expected artifact missing、
unexpected Git state。

Recovery policy 依 structured error 決定 retry、fallback、bounded repair、request approval、
stop 或 user action required。Objective escalation 與 provider fallback 分開設計：記錄
schema failure、missing evidence、validator reject、test failure、review disagreement 或
bounded repair exhausted，以及精確 evidence reference/被授權的下一步；不能依 confidence。
升級到外部模型、更多 context 或人員前重新檢查資料範圍、destination 與 provider trust
policy；無授權就停止，不得用「cheap-first」繞過 policy。Bounded repair 每次仍回到
contract/policy/required approval gate；耗盡後保留失敗證據與成果，不放寬 criteria。

每個 run 必須有 explicit budgets：

- max provider calls；
- max repair attempts（MVP 必須是小的固定上限，例如 2；實際值在 implementation spec 定案）；
- wall-clock timeout；
- input/output token budget where provider reports usage；
- optional monetary budget 只能在價格資料可信且明確設定時啟用，不可自行估價當作 hard truth；
- total usage 包含 preparation/retry/repair/escalation/review；缺 usage/pricing 時標示 unknown，
  不視為零或已符合 monetary budget。Context budget/cost routing 實作待後續設計；
- repeated-action detector 必須有 deterministic comparison rule。

AUTH/CONFIG/SAFETY 等不可恢復錯誤不得 blind retry。

### A11b.6 — Failure Injection & Safety Tests

故意覆蓋 401/403/429/5xx、timeout、network unreachable、provider unavailable、
malformed/empty model response、invalid tool call、tool nonzero exit、permission denied、
missing executable、dirty worktree、validation/test failure、repeated loop，以及
secret-looking data 出現在 stderr/tool output 的 redaction。

每一類 failure 都要驗 stable error code、normal CLI、trace/audit、policy decision、
STOP/recovery behavior、stdout/stderr contract 與 secret safety。

### A11b Definition of Done

- 有限 executor 在 sandbox/policy 下可完成一個安全 mutation scenario。
- Git hardening 沒有退化 A5 boundary。
- TTY/non-TTY approval 行為可驗證且 fail closed。
- patch failure 不留下未知 partial state。
- resource budgets 與 bounded repair 有 deterministic tests。
- Task Contract version/scope enforcement、claim→evidence verification、客觀升級與 trust
  拒絕路徑有 failure tests；模型不得改 criteria，缺 required evidence 不得 VERIFIED/DONE。
- 所有 mutation 都有 audit trail。

## A11c — Integration & Real-world Acceptance

### A11c.1 — Production Workflow Integration

A11c 不採 blanket `company-first`。正式 integration 使用 **route-by-task evidence**：
每個 task class 都要有自己的 reliability/quality/usage evidence，不能因同一 provider 在另一個
route PASS 就推廣授權。

目前只有 `test-triage` 在 synthetic L01 fixture 上有 quality-approved token-saving evidence；
`log-triage` 與 `context-pack` 仍是 EXPERIMENTAL。後兩者可以作 bounded development aid，
但在新的 Company-only gate PASS 前不能成為 A11c token-saving production route。

A11c production router 延續三層順序並正式化為可驗證 policy：
**deterministic/no-model lane → route-authorized company-LLM lane → strong verifier lane**。能由
deterministic helper 取得的 repo/GitHub/CI/test state 不應消耗模型推理；只有已通過該 task
class 自己 gate 的 bounded/低風險分類與壓縮才能進 company lane。security/auth/concurrency/
root-cause/merge 等高判斷工作以及 objective escalation 交給較強 verifier。不得以 model
self-confidence 選 lane，也不得用 token 價格低作為 capability/authorization 證據。

新 route 的 A11c 前置 gate 固定為：Company-only repeated Phase 0 → deterministic/independent
quality PASS + complete valid usage → 才允許 paired strong-model measurement。Gate FAIL、
incomplete output 或 accounting unknown 都停止，不得先燒 Codex 再補 reliability evidence。
Provider 不可用時如實記錄 environment/auth 限制，再依 objective escalation 與 trust policy
處理；不默認切到昂貴外部模型。Schema validation 只確認結構，內容仍需原始 source/evidence
比對與獨立驗證。

GitHub/CI integration 若需要新增 collector，應輸出 bounded structured state（例如 repo/
branch/head/ahead-behind/dirty、PR/head/mergeability、required CI checks）而不是把整段
`gh`/CI raw output 塞給模型。這個 collector 是 deterministic capability，不是新的 agent，
也不因資料收集成功而授權 Git mutation。

工作 context 可壓縮，但每次 handoff 固定三層：不可縮寫取代的 pinned contract、可壓縮
working context、可追溯的 structured evidence。摘要必須保留 source/revision 引用、unknown、
forbidden scope、未解決問題、矛盾與必要 evidence references；原始證據保留供驗證者查閱。
Context overflow 時做有界分段／引用或回報 INCOMPLETE，不得靜默刪限制或未解 findings。

第一輪 company review 產生 findings/claims；獨立驗證者對照 contract、完整相關 diff 與
原始證據再判定，不因開發者或模型宣告完成而 PASS。Bounded repair 使用 A11b budgets/
gates，修正後重驗受影響 criterion；超過上限仍有 blocker 就停止並回報。

將 A0～A10 + A11a/A11b 組成 bounded end-to-end workflow：
task → context → plan/router → policy/required approval → execution → validation；
repair proposal 與 Git action 各自回到 policy/required approval，通過才執行 → final result。

A9 text orchestration、A10 retrieval、A6 validator、A5 Git intelligence 的既有 boundary
不得被 integration 偷偷放寬。

### A11c.2 — Real-world Agent Acceptance Test

最後 gate 使用真實 repository/task，至少覆蓋：

- 正常 bugfix + real tests；
- provider timeout + policy-allowed fallback；
- policy-blocked cross-trust fallback；
- dirty worktree STOP；
- model 宣稱完成但 tests fail；
- schema 合格但摘要漏 source/unknown/限制、或內容與原始 evidence 矛盾；
- missing required evidence／NOT-TESTED check 阻擋 VERIFIED/DONE；
- review disagreement 或 repair exhausted 觸發客觀升級，外送敏感摘要遭 policy 拒絕；
- contract 變更未獲使用者授權、或新版本誤用舊 revision 的 PASS；
- total usage 包含 review/retry/escalation，未知 token/cost 不填零；
- tool failure + bounded repair；
- dangerous/unknown action approval/block；
- non-interactive approval fail closed；
- long context + retrieval；
- run ID / trace root-cause diagnosis。

產生 dated acceptance report。必要驗收條件：另一個 reviewer/agent 僅使用 run metadata +
trace（不依賴原開發者記憶）即可定位一個失敗 run 的主要 failure layer。

### A11c Definition of Done

任何 production workflow 失敗都有 stable error code + unique run ID；normal mode 知道
發生什麼與去哪查；trace 能定位 component/provider/tool；可恢復錯誤依 policy
retry/fallback/repair，不可恢復錯誤 fail closed；AI 成功聲明經 deterministic validation；
diagnostics/audit 不洩漏 secrets；resource/retention budgets 有界。驗收還需證明 route-authorized
company handoff 保留 pinned contract/source/unknown/evidence，獨立 verification 不接受無證據 claim，
且客觀升級與 bounded repair 不降低驗收標準。Coverage limitations 與 unknown cost 如實記錄；
不宣稱所有語意正確，也不以尚未設計的 cost router 宣告成本最佳化完成。

## A12 — Remote Frontends / Telegram Agent

未來附加功能：llmtool 執行在使用者的筆電或桌上型電腦，使用者可透過手機
Telegram Bot 遠端與 llmtool 對話、送出自然語言指令與控制命令，並建立 llmtool task。
A12 不影響目前 A3 / A3.5 / A4～A11 的主線開發；目前只列入 roadmap，不實作
Telegram 程式碼、不新增 dependency、不修改 production code。

**Telegram frontend != llmtool core**。
**CLI、Telegram、未來 Web UI 應共用同一套 llmtool runtime。**
**A12 MUST consume A11 的 task/run/event/error/approval model；不得建立第二套 logging、execution、approval 或 error system。**
Telegram 只是 frontend / gateway，不重新建立另一套 Agent、Task 或 workflow 邏輯。
必須共用 llmtool 原本的 Session、Task、Skill、Provider、Role、Agent、Approval、Event；
尚未實作的 core 能力由主線 milestone 建立，frontend 不另行複製。
Provider != Role != Agent != Skill；遠端入口不因 transport 或 provider capability 而
隱含授予執行權限。

第一版目標：

- 優先採用 Telegram Bot API long polling；不要求 host 開 public port，也不要求 webhook。
- 支援自然語言 task 建立與控制命令；`/status` 可查看 host、session 與 task 狀態。
- Bot 透過共用 Event 主動回報 `task.created`、`task.started`、`task.progress`、
  `task.waiting_approval`、`task.completed`、`task.failed`。
- 遠端 approval 支援 `/approve <task_id>`、`/reject <task_id>`；Telegram inline button
  approval 列為目標。task_id 只用於定位 pending action，確認介面必須顯示 exact action
  並沿用 A11b 的 task/run/action/state 綁定、有效期與防 replay；不能批准整個 task 的未知後續操作。
- 必須有 Telegram user/chat allowlist，並將操作權限分為 read-only、approval-required、
  dangerous；dangerous 操作不得因遠端訊息而自動獲准，須依 core policy 與 approval 處理。
- Bot token 不得寫進 repository；由 host 的安全設定提供，不在狀態或事件回報中暴露。
- host 離線或 llmtool 無法連線時，必須呈現清楚的 offline / unreachable 狀態；
  不把未送達的 task 或 approval 回報為成功。host 離線時的可達性偵測／提示方式需在
  實作設計中明確定義，不能假設離線 host 上的 Bot 仍能即時主動回報。

Frontend 擴充方向：CLI / Interactive Chat、Telegram、Web UI。各入口共用 runtime
與相同的 task lifecycle、事件及 approval 語意，不另建獨立的 orchestration。

## 未來 config 方向（目前 schema 尚未接受此完整形式）

```yaml
providers:
  qwen-office:
    type: openai-compatible
    base_url: "http://llm.example.test:8000"
    model: "example-model"
  openai:
    type: openai
    model: "example-openai-model"
  anthropic:
    type: anthropic
    model: "example-anthropic-model"
  claude:
    type: claude-cli
    command: claude
  codex:
    type: codex-cli
    command: codex
roles:
  grinder: qwen-office
  validator: openai
  coder: claude
  alternate_coder: codex
```

A1 保留 named provider 與共用 response contract 作為擴充點，但拒絕未支援 type、
roles、command 等欄位。此 roadmap 不加入 RAG、vector DB、embeddings 或 fine-tuning。
整體方向由 A3.5 Interactive Chat Shell 提供互動入口，主線逐步建立至 A11 Production Workflow，
再以 A12 Remote Frontends / Telegram Agent 擴充遠端入口；A12 是未來附加功能，不改變
既有 milestone 的完成狀態、驗證紀錄或主線開發順序。

## Post-A9 Hardening ✅ DONE and merged to main

A0–A9 DONE and merged；Post-A9 hardening 亦已透過 PR #11 合併到 `main`
（merge commit `5f581b46968a2dd09d808b3d3125beb4597c0fc1`）。修正 kubectl connection/auth/impersonation
approval floor、nonblocking stdin conflict、configure preservation、A9 explicit-profile isolation、Unicode
text controls、analyze-log credential-display guard，以及 final F1 credential-source gap。Plain kubectl
SAFE_READ 的前提是 trusted kubeconfig/context 且沒有連線／認證／impersonation overrides。
analyze-log 從不執行 NEXT_COMMANDS；不安全或不支援的建議只會以可見 marker 抑制，不會丟掉可用分析。
External ChatGPT review PASS；Claude re-review APPROVE；final reviewed head `566cdb5` 的 Python 3.9 / 3.14
CI 各 552/552 PASS。M6 preferred_verify_next_any = DEFERRED（semantic equivalence、exact curated evidence、
prompted vs unprompted evidence 需要獨立設計決策）；保留 A8 criteria 和 Claude measured model safety FAIL。
A10 已 DONE and merged；Anthropic HTTP API 仍是 future A2.3。
詳見 [hardening validation](../../reports/llmtool_post_a9_hardening_validation_20261001.md)。
