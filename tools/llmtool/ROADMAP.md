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

## Canonical repository workflow

All project development, tests, validation, documentation, and Git operations
must run from `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Do not create or use worktrees, independent or milestone clones, sibling or nested
repositories, or alternate project checkouts. The previous isolated project
checkout workflow is deprecated and must not be reused. See [AGENTS.md](../../AGENTS.md).

If sandbox permissions block `.git` writes, leave changes in the canonical
repository and report exact host-terminal staging/commit/push commands; do not
create another checkout. Preserve unrelated user work. Stop for external review
when required; do not automatically start the next milestone or merge main.

All unit, targeted, canonical, smoke, provider integration, regression, Git, and
documentation validation must be launched from the canonical repository.
Test-generated temporary files are allowed only when required by the test/runtime.
The `codex-cli` provider's ephemeral cwd under `/tmp` or `/var/tmp` remains a
runtime security mechanism, not a project checkout. It must contain no Git clone,
worktree, or copy of this repository. This policy does not change provider behavior.

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
A9 不自動跑 benchmark。A10 Dynamic Context Retrieval 尚未開始。

`llmtool orchestrate --plan ... --dry-run` 僅做 plan structural validation，不讀 task、
config、credentials，也不建 provider client。Normal mode 才依序 resolve explicit
profiles 並各呼叫 Provider.complete 一次。

Anthropic HTTP API 仍屬 A2.3 planned/unimplemented；A9 不 special-case Anthropic，
未來 provider registry 加入後即可由同一 plan/profile contract 使用。

A9 DONE and merged via PR #10 (`8776476`)；A9-R1 FIXED，updated external ChatGPT review PASS，
final-head Python 3.9/3.14 CI PASS。A10 NOT STARTED。

## A10 — Dynamic Context Retrieval

Task → first-pass model → identify relevant files → fetch missing evidence →
second-pass context packet。取得證據的權限與範圍需明確。

## A11 — Production Workflow

Git / Logs / Files → llmtool → provider / grinder → context packet → validator →
Claude/Codex → patch → tests → human approval where required → Git。

此為完整流程目標。目前 A0～A9 已完成並合併至 `main`；A9 只建立 bounded text orchestration，仍沒有 production mutation／approval execution。

## A12 — Remote Frontends / Telegram Agent

未來附加功能：llmtool 執行在使用者的筆電或桌上型電腦，使用者可透過手機
Telegram Bot 遠端與 llmtool 對話、送出自然語言指令與控制命令，並建立 llmtool task。
A12 不影響目前 A3 / A3.5 / A4～A11 的主線開發；目前只列入 roadmap，不實作
Telegram 程式碼、不新增 dependency、不修改 production code。

**Telegram frontend != llmtool core**。
**CLI、Telegram、未來 Web UI 應共用同一套 llmtool runtime。**
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
  approval 列為目標，並沿用 core Approval 的 task 綁定與權限檢查，不繞過 approval。
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
A10 NOT STARTED；Anthropic HTTP API 仍是 future A2.3。
詳見 [hardening validation](../../reports/llmtool_post_a9_hardening_validation_20261001.md)。
