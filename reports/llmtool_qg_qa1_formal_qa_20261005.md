# llmtool QG-QA1 — Formal Product / User-facing QA（2026-10-05）

## 1. Executive summary

這一輪把 `origin/main` `8d3eb86` 上的 llmtool 當成真正的產品，從使用者的角度操作：安裝 → 設定 → 選 provider → 輸入 → 取得輸出 → 查看 diagnostics → 錯誤、signal、並發。

**結論：日常使用的主要功能都正常。** 以下都實際走過、並且通過：

- 19 個指令和 33 個 help；
- 4 種 provider，包括公司 gateway、真實 claude-cli、真實 codex-cli 的 live 測試；
- 所有輸入形式；
- 分析類指令：`analyze-log`、`review-diff`、`context-pack`、`retrieve-context`、`orchestrate`；
- diagnostics：儲存、讀取、清理和並發；
- DG-1 的 signal regression；
- 7 個端對端的使用者流程。

沒有發現資料損壞、永久卡住、自動 retry 或 fallback、隱私外洩，也沒有任何流程完全壞掉。

**但依照本輪的定義（任何 REQUIRED case FAIL 就是 FAIL），結果是 FAIL。** 有 3 個 REQUIRED case 失敗，嚴重度都是 Low 或 Low–Medium：

1. **Q-BUG-1（CONFIRMED，Low）：** 錯誤訊息標錯子系統。`orchestrate` 和 `benchmark` 的 plan、suite 格式錯誤時，訊息寫的是 "feedback JSON is malformed" 之類；明確指定的 profile 不存在時，訊息寫的是 "active provider is not defined"。
2. **F1（QA FAILURE — BUG CANDIDATE，Low–Medium，contract gap）：** 開啟儲存時收到 TERM 或 HUP，run 不會被 finalize；在 `max_runs` 很小的情況下，最多 1 小時內每次都顯示 `DIAGNOSTICS_STORE_FULL`。
3. **D-34（QA FAILURE — BUG CANDIDATE，Low）：** 啟動後約 0.2 秒內按 Ctrl-C，會印出原始的 Python traceback（含 host 路徑），exit 是 -2，不是 130。

另外還有 2 個 OPTIONAL 的失敗：非 A11a 指令在小輸出時遇到 broken pipe 會 exit 120。這是 README 已寫明的已知限制。

```text
QA CASES TOTAL: 271
PASS: 260
FAIL: 5
BLOCKED: 0
NOT-TESTED: 6
CONFIRMED FUNCTIONAL BUG COUNT: 1
NEW SECURITY REVIEW CANDIDATES: 0
QG-QA1 RESULT: FAIL
READY FOR COORDINATOR TRIAGE: YES
```

## 2. Exact revision

| 項目 | 值 |
|------|-----|
| `origin/main`（`git fetch --prune` 之後） | **`8d3eb8648fe58031ad3b812039c71703e1bbe3c4`**（PR #29 DG-1 的 merge commit） |
| post-merge CI | `37256086556`：success，headSha 就是 `8d3eb86` |
| 測試時的 checkout | canonical repo，**detached 在 `8d3eb86`**，沒有移動任何分支 |
| working tree | 只有使用者原本就有的未追蹤檔案（`reports/llm_benchmark_*`、`reports/llm_reasoning_probe_*`），以及之前留下的 `tools/llmtool/case.*` |

## 3. Environment

- macOS 15.7.9（24G830），x86_64。
- Python：3.13.3（`tools/llmtool/.venv`，主要的 runtime）、3.9.6（`/usr/bin/python3`）。3.14 只透過 CI 驗證。
- PyYAML 6.0.x。
- 公司 gateway `http://home-llm.example:8000`：可連線（HTTP 200）。
- 真實的 claude CLI 2.1.289、codex-cli 0.159.2：使用者既有的登入可以使用。
- 每個 case 都在 scratchpad 內各自用 `mktemp -d` 建立、以 scratchpad 為工作目錄執行，使用 `env -i`、獨立的 `HOME` 和 `LLMTOOL_HOME`。

## 4. QA strategy

1. 先閱讀 README、AGENTS、CLAUDE、ROADMAP、WORKFLOW、`skills/qa-validation`、`validate-runtime`、`bug-hunt`，以及 QG-S1、DG-1、A11a 相關的報告。
2. 從 `--help` 整理出 public CLI 的清單（§6），再建立 QA matrix（§5）。**每一組在執行前就先定好 REQUIRED／OPTIONAL／ENV-DEPENDENT。**
3. 依 bucket 平行執行（A～D 四組，由 4 個 QA agent 透過 public CLI 執行）。coordinator（我）負責 live provider、openai mock、端對端的使用者流程、security smoke、cross-environment，並**親自重現**每一個 FAIL。
4. 只使用 public CLI（或與它等價的 `cli.main()` 入口）。unit test 只當作 baseline，不能取代 QA。
5. 和目前契約不一致的 case，依現行文件調整，並記錄原因（§7）。

## 5. QA matrix（摘要）

| Bucket | 涵蓋的需求章節 | REQUIRED case 數 | 結果 |
|--------|---------------|-----------------|------|
| A：安裝、啟動、providers、config、configure wizard、skills、錯誤訊息 | 1、2、3、4、7、21 | 46（另有 OPT／ENV 6） | 50 PASS、2 NOT-TESTED |
| B：輸入、ask、analyze-log、review-diff、context-pack、輸出與 terminal、HTTP provider | 5、6、20、22 | 約 65（另有 OPT／ENV 16） | 79 PASS、2 FAIL（OPT） |
| C：chat、git、validate、feedback、benchmark、orchestrate、retrieve-context | 8–14 | 約 64（另有 OPT 7） | 70 PASS、1 NOT-TESTED |
| D：diagnostics、runs／trace／doctor、F1、並發、process lifecycle | 15–19 | 49（另有 OPT 1） | 48 PASS、2 FAIL（REQ） |
| CO：live provider、openai mock、端對端流程、security smoke、cross-env、錯誤訊息驗證 | 22–25、21 | 14（另有 ENV 3） | 13 PASS、1 FAIL（REQ）、3 NOT-TESTED |

完整的逐項表格放在 §8–§24，依 bucket 排列。每個 case 都有 evidence 路徑：
- 根目錄是 `$Q=/private/tmp/claude-501/-Users-davidsun-git-davidcataiporject-davidcattoolsproject/88ee90aa-8e62-4eeb-a67b-e373aeba2609/scratchpad/qa1`；
- 子目錄 `A/`、`B/`、`C/`、`D/` 對應各 bucket，`J/` 是 coordinator。

## 6. Public CLI inventory

```text
ask, analyze-log, review-diff, context-pack, chat, git [--json],
feedback {capture, show, validate, review, regression},
benchmark {evaluate, live, suite},
orchestrate --plan [--file] [--json] [--dry-run],
retrieve-context --root --scope --planner-provider --synthesizer-provider [--file] [--json],
doctor, runs, trace <run_id> [--errors] [--json],
validate {command, context}, configure, providers [--json], skills [--json], skill show <name>,
config {init [--force], show, path}, --version, --help
```

共 19 個 top-level 指令，加上子指令共 33 個 help 目標（A-04 全部是 rc 0，不需要 config，也不會寫入 HOME）。

**主要的使用者流程：**
1. 第一次設定；
2. 單次問答；
3. 事故分析；
4. 程式碼 review；
5. 多 profile 切換；
6. 失敗之後查看 diagnostics；
7. 互動式 chat；
8. 離線的分類、驗證和 benchmark。

## 7. Required／Optional／Environment-dependent 的判定，以及 case 調整記錄

- **REQUIRED：** README／ROADMAP 明確承諾的行為，包括輸出 schema、exit code、fail-closed、request 次數、唯讀和不執行的保證。
- **OPTIONAL：** 契約以外的邊界或 hardening，或文件寫明的已知限制。
- **ENV-DEPENDENT：** 需要特定前提，例如 pty、公司 VPN、真實 CLI 的登入、沒有 git 的 `PATH`。

**依現行契約調整的 case：**

| 原本的需求 | 調整 | 原因 |
|-----------|------|------|
| `providers` 輸出要顯示「capability ≠ authorization」 | 改成確認 README 有這段說明；CLI 只輸出 yes/no | 契約把這段放在文件，不在 CLI 輸出（A-13） |
| SIGTERM／SIGHUP 對 HTTP provider 要 143／129 | 改成「預設的 disposition，沒有 traceback」 | README 只對 **CLI provider** 承諾 143／129（B-66、B-67、D-35） |
| ask 以外的指令遇到 broken pipe 要 exit 0 | 列為 OPTIONAL | README Troubleshooting 寫明舊指令的 broken pipe 仍是 legacy 行為 |
| `orchestrate --dry-run "task"` | 改成只傳 plan | 契約寫明 dry-run 不讀 task；傳入 task 時被拒絕是正確的 |
| 使用者自訂 skill（malformed skill） | 改成確認「只使用打包的 skill」，並在 scratch venv 的安裝目錄做 malformed 測試（A-43，OPT） | 契約沒有 user skill discovery |
| openai provider 的 mock | CLI 沒有改 origin 的方法，所以透過 `cli.main()` 入口、只把 `API_ORIGIN` 指向 local mock 來測（CO-12） | openai 的 origin 是刻意固定的 |

## 8. Installation／startup（Bucket A）

| ID | Req | Case | 結果 |
|----|-----|------|------|
| A-01 | ENV | 在全新的 scratch venv 中 `pip install --no-deps --no-build-isolation`，console script 可以執行 | PASS（離線環境只有 setuptools 67.6.1，低於宣告的 `>=68`，所以用 Python 內建的 test wheel 安裝 setuptools 和 wheel；見 §29） |
| A-02 | REQ | `--version` | PASS（`llmtool 0.1.0`） |
| A-03 | REQ | top-level `--help` 列出 19 個指令 | PASS |
| A-04 | REQ | 33 個 help 目標 | PASS（全部 rc 0，HOME 保持空的） |
| A-05 | REQ | unknown command、invalid option、沒有指令、缺少位置參數、save 和 no-save 同時指定、ask 專用的 flag 用在其他指令 | PASS（rc 2，`invalid command or options; use --help`） |
| A-06 | REQ | 把 marker 當成 invalid option 的值、指令名稱或 option 名稱 | PASS（stdout 和 stderr 都沒有 echo） |
| A-07 | REQ | `--max-tokens QAMARK` | PASS（`CONFIG_INVALID`，沒有 echo） |
| A-08 | REQ | 沒有 PyYAML | PASS（help、version、providers 照常；需要 config 的指令提示「PyYAML is required」） |
| A-09 | OPT | 從 repo 路徑執行，以及用 Python 3.9 執行 | PASS |

## 9. Config／configure UX（Bucket A）

| ID | Req | Case | 結果 |
|----|-----|------|------|
| A-10～A-12 | REQ | `providers`／`--json`：4 個 provider，輸出穩定；不讀 config、不建立檔案；壞掉的 user 或 project config 不影響 | PASS |
| A-13 | REQ | capability 的措辭（已調整，見 §7） | PASS |
| A-14～A-16 | REQ | `config path`；沒有 config 時的 `show`；`init`、重複 `init`、`--force`（檔案 0600、目錄 0700，重複時提示 `--force`） | PASS |
| A-17 | REQ | config 是 dangling symlink 時拒絕 `init` | PASS |
| A-18 | REQ | redaction：key 顯示 `***REDACTED***`，`api_key_env` 只顯示變數名稱 | PASS |
| A-19～A-24 | REQ | malformed YAML、duplicate key、alias、超過 64 KiB、目錄、無法讀取；不支援的 provider type 或欄位；model、endpoint（ftp、`/v1`、含 credential、含 query）；缺少必要欄位；各種欄位驗證 | PASS（固定訊息、不 echo；未設定時是 `CONFIG_PROVIDER_NOT_READY`） |
| A-25～A-27 | REQ | 優先順序 CLI > env > project > user；project config 的禁止欄位；多 profile 的實際 request 內容 | PASS |
| A-28～A-36 | REQ | configure wizard（pty）：openai-compatible、openai、claude-cli、codex-cli 問的問題正確（claude 和 codex 不問 URL 或 key）；保留既有的 profile；active 的選擇；Ctrl-C → 130 且沒有儲存；EOF → 沒有儲存；不合法的答案；編輯（空白保留、`-` 清除）；malformed config 或 symlink 拒絕；非 TTY 時拒絕隱藏輸入 key | PASS |
| A-37 | OPT | claude-cli 從非 TTY 設定 | PASS（和文件「需要 TTY」不完全一致，見 UX） |

## 10. Input UX（Bucket B，透過真實的 CLI）

| ID | Req | Case | 結果 |
|----|-----|------|------|
| B-01～B-04 | REQ | 位置參數、`--file`、stdin pipe、stdin redirect | PASS |
| B-05 | REQ | `while read q; do ask "$q"; done < file` | PASS（3 次 request，每次是各自的問題） |
| B-05b、B-05c | OPT | `cat file \| while …`（依契約是衝突）／加上 `</dev/null` | PASS（UX：可以在文件提示解法） |
| B-06～B-09 | REQ | 明確的輸入 + 空 stdin 或 `/dev/null`：OK；+ 非空 pipe 或 `--file`：衝突，request 為 0 | PASS |
| B-10～B-15 | REQ | 空的、只有空白；UTF-8、中文、emoji、tab、換行、ANSI（逐位元組相同）；NUL、BEL、SOH、invalid UTF-8 被拒絕 | PASS |
| B-16～B-19b | REQ／OPT | 目錄、FIFO（立即拒絕，不會卡住）、symlink（跟隨明確指定的 symlink）、dangling、不存在、mode 000 | PASS |
| B-20～B-24 | REQ／OPT | 剛好 16 MiB 可以接受；16 MiB + 1 byte 被拒絕（file、pipe、redirect）；16 GiB 的 sparse file 快速被拒絕 | PASS |

## 11. Core command QA（Bucket B）

| ID | Req | Case | 結果 |
|----|-----|------|------|
| B-30～B-34f | REQ | `ask` 的 verbose、debug、`--skill`、`--provider`（含 enable_thinking 的傳遞）、`--model`／`--max-tokens`／`--temperature`／`--timeout` 的覆寫和驗證、timeout、usage 錯誤 | PASS |
| B-40～B-59 | REQ | HTTP 400、401、403、404、429、500、502、503、302（不跟隨）；malformed JSON；沒有 choices 或 content；`finish_reason=length`、`content_filter`、`tool_calls`；回應超過 16 MiB；`reasoning_content` 被省略；不合格的 metadata 被省略；無法連線 | PASS（每次都只有 1 次 request，錯誤內容不會被顯示） |
| B-60、B-61 | REQ | `ask` 的 broken pipe（小輸出和大輸出） | PASS（rc 0，stderr 是空的） |
| **B-62、B-64／64b／77** | OPT | `analyze-log`、`context-pack`（`--json` 和 human）、`review-diff` 的**小輸出**遇到 broken pipe | **FAIL**（rc 120，`Exception ignored … BrokenPipeError`）；README 已寫明是 legacy 限制 |
| B-62b、B-63、B-64c | OPT | 同樣的指令、大輸出 | PASS |
| B-65～B-67 | REQ | HTTP 請求中途收到 SIGINT → 130；TERM、HUP → 預設的 disposition（依契約調整，見 §7） | PASS |
| B-80～B-96 | REQ | `analyze-log`：明確的 root cause、干擾錯誤、證據不足、`(none)`、`rm -rf`／`kubectl delete`／`touch CANARY` 被 suppress（canary 沒有被建立）、讀取 secret 的建議被 suppress、最多 3 個建議、fenced block、超過 1 MiB、`ask --skill analyze-log` 也套用同一個 guard | PASS |
| B-100～B-105 | REQ | `review-diff`：只送出提供的 diff（PATH 上放假的 `git`，它從未被呼叫；dirty worktree 的 canary 沒有出現在 request）；乾淨的 diff；prose；空的；16 MiB 的邊界 | PASS |
| B-110～B-117 | REQ／OPT | `context-pack`：human 和 `--json`、`--json --verbose`（stdout 只有 JSON）、矛盾的事實、只有 unknowns、11 種 malformed 輸出、超過 1 MiB | PASS（B-116 有 UX 註記） |

## 12. Skills／chat／git／validator（Bucket A、C）

- **skills（A-38～A-43）：** 全部 PASS。`skills`、`--json`、3 個打包的 skill、unknown 或 traversal 被拒絕、只有打包的 skill 會被發現、`ask --skill` 的 system message 正確、malformed skill 被拒絕。
- **chat（C-101～C-111）：** 全部 PASS，使用 pty。
  - 多輪對話時，request 的 message 數是 2、4、6，代表 context 有保留。
  - EOF、提示符號時的 Ctrl-C、request 進行中的 Ctrl-C 都正確處理。
  - 第 2 輪遇到 provider 500 時，會丟掉那一輪、session 繼續。
  - `/skill`、`/provider`、`/clear` 正常；回覆中的「approved… touch X」不會被執行。
  - 結束後 HOME 和 LLMTOOL_HOME 的檔案樹不變，TTY escape 也正確。
  - coordinator 也用公司 gateway 做了 live 兩輪測試（CO-09）。
- **git（C-201～C-211）：** 全部 PASS。clean、dirty、staged、untracked、branch、detached、子目錄、unborn repo、不是 repo、PATH 沒有 git、惡意檔名和 hook／fsmonitor／pager 都正確處理；`.git` 的 checksum 前後完全相同。
- **validate（C-301～C-31x）：** 全部 PASS。
  - command：SAFE_READ、MUTATING、DESTRUCTIVE、SENSITIVE_READ、UNKNOWN、`$( )`、redirect、控制字元、超過 8 KiB 都正確分類；`touch` marker 沒有被建立。
  - context：valid、malformed、各種結構錯誤、超過 1 MiB、深度、NaN 都正確處理。

## 13. Feedback／benchmark（Bucket C）

- **feedback（C-401～C-413）：** 全部 PASS。capture 是 deterministic 的、檔案 0600、不覆蓋；10 種不合法的輸入被拒絕；show 和 validate 正常；版本相容性正確；review 是 atomic replace、只能 review 一次；regression assertion 正常；沒有執行任何指令。
- **benchmark（C-501～C-514）：** 全部 PASS。
  - evaluate 的 PASS 和 FAIL 都正確，安全檢查會失敗在 mutating 或 destructive 的建議；
  - runtime metadata、suite 的彙總、malformed manifest 和 deterministic 結果都正確；
  - `live` 只發 1 次 request，失敗時不 retry，沒有 config 時 request 為 0。

## 14. Orchestration（Bucket C + CO-06）

- **C-601～C-612：** 全部 PASS。
  - 沒有 config 時，dry-run 是離線的；
  - 3 個 role 依序執行，每一步只拿到原始 task 和上一步的輸出；
  - 中間一步回傳 500 → 停止，後面的步驟 request 為 0；malformed 或空的輸出 → 停止；
  - 不合法的 plan 在呼叫任何 provider 之前就被拒絕；
  - `LLMTOOL_*` 環境變數不會改寫綁定；
  - 有上限；
  - 模型輸出「APPROVED… rm -rf… add coder step」不會取得任何權限；
  - TTY escape 正確。
- **live（CO-06，公司 gateway）：** grinder → reviewer 兩步驟正常，`--dry-run`／`--json` 正常。

## 15. Retrieval（Bucket C + CO-06）

- **C-701～C-721：** 70 個 PASS，C-716 列為 NOT-TESTED（沒有獨立執行，但每個 C-7xx case 都做了檔案樹快照，內容從未改變）。
  - 每次剛好 2 次 request；planner 只看到路徑和大小；synthesizer 只收到被選取的檔案；
  - 重疊的 scope 會被去重；
  - unknown、超出 scope、traversal、`.env`、symlink、超過 8 個檔案、重複 → 在讀取之前就失敗；
  - binary NUL 被拒絕；300 KiB 的檔案和 `.png` 不會出現在清單中；
  - 11 種 credential 類的路徑都被排除；
  - Unicode 和控制字元會被 escape；
  - provider 失敗時沒有 partial output；
  - root 的各種不合法形式都被拒絕。
- **live（CO-06）：** 公司 gateway 上的 human 和 `--json` 輸出都正常；`.env` 的內容沒有出現在輸出中。

## 16. Diagnostics（Bucket D）

| ID | Req | Case | 結果 |
|----|-----|------|------|
| D-01、D-02 | REQ | normal、verbose、debug 三種模式成功或失敗時，stdout 都完全相同 | PASS |
| D-03 | REQ | 預設不儲存（有沒有設定 `LLMTOOL_HOME` 都一樣） | PASS |
| D-04 | REQ | `--save-diagnostics` → `runs`／`trace`／`trace --json`，summary 的 12 個欄位都完整，檔案 0600、目錄 0700 | PASS |
| D-05 | REQ | 在 store 裡搜尋 prompt、回答、`sk-` key、錯誤內容、reasoning、stderr、host、`/Users/` 共 9 種 marker | PASS（0 筆） |
| D-06 | REQ | `--no-save-diagnostics` 可以蓋過 user config 的 opt-in | PASS |
| D-07 | REQ | 7 種壞掉的 store × 成功或失敗 × 3 種模式，共 42 次 | PASS（stdout 和 exit 都不變，只有一筆固定的 warning） |

## 17. runs／trace／doctor 與 F1 triage

| ID | Req | Case | 結果 |
|----|-----|------|------|
| D-08～D-15 | REQ／OPT | 空的 store；1 個或多個 run 的排序；error、interrupted、output_closed、incomplete 狀態；trace 的各種 ID 和狀態；損壞的 summary 或 trace；105 個 run 時截斷到 100 個；健康的 store；active run 加上 FULL | PASS |
| D-16～D-22 | REQ | abandoned run、0755、malformed reservation、殘留的 artifact、權限問題、symlink、不合法的 `LLMTOOL_HOME` | PASS（行為符合文件；有 UX 註記） |

### F1 正式 triage（D-F1，REQUIRED）

- **重現：** user config 設定 `persist_metadata: true`，使用 claude-cli 的慢速 fake，執行 `ask` 時送出 TERM 或 HUP；接著執行 ask × 2、`runs`、`doctor`；然後用 `os.utime` 把 abandoned run 的時間改回 3700 秒前，再執行 `ask` 和 `runs`。也測了 `max_runs: 1` 和預設的 100。
- **觀察（TERM 和 HUP 相同）：**
  - exit 是 143／129；即使加上 `--verbose`，stderr 也是空的（沒有 run_id，也沒有 persisted 的資訊）；process tree 和暫存目錄都被清除。
  - run 目錄裡只有 `.reservation`、`.run.lock`、`trace.partial.jsonl`，**沒有 summary**。
  - `runs` 顯示 `incomplete … provider_type=unknown`；`trace` 顯示 `state=incomplete`；`doctor` 顯示 `run_count=1 active_count=0`，**沒有任何 abandoned 的提示**。
  - **`max_runs: 1`：** 之後每次 ask 都是 `DIAGNOSTICS_STORE_FULL`、`diagnostics_persisted=false`；答案和 rc=0 都正確。
  - **預設的 `max_runs`：** 可以正常儲存，只是那個 run 佔用一個名額。
  - **模擬 1 小時的 grace period 之後：** 下一次 ask 會把 abandoned run 清掉，並且成功儲存；沒有任何已 commit 的 metadata 遺失。
  - **對照 SIGINT：** exit 130，run 被 finalize 成 `interrupted`，下一次就能儲存。
- **對照契約：** README 只寫「Ask interrupts finalize with `interrupted`」「A crash … can leave a partial trace」「abandoned runs have a one-hour grace」，**沒有承諾 TERM／HUP 會被 finalize**。不過 README 對 CLI provider 的 TERM／HUP 用的是「cancellation，清理後結束」，也就是一條被處理的正常路徑，而不是 crash。
- **分類：QA FAILURE — BUG CANDIDATE（contract gap，Low–Medium，不是 regression）。**
  - 從使用者的角度來看：llmtool 已經能處理這個 signal，卻留下一個「不 active、但也沒有完成」的 run；`max_runs` 很小時，最多 1 小時無法儲存；顯示的 `FULL` 有誤導性，`doctor` 也沒有提示。
  - 這不影響主要的結果，也會自動恢復，所以**不是 confirmed functional bug，也不是 blocker**。
  - 建議二擇一：（a）把 TERM／HUP finalize 成 `interrupted`；（b）在 README 明確寫出這個行為，並讓 `doctor` 回報 abandoned 的數量。

## 18. State／concurrency（Bucket D）

| ID | Req | Case | 結果 |
|----|-----|------|------|
| D-30 | REQ | 同時執行 8 個 ask（不儲存） | PASS |
| D-31 | REQ | 同時執行 8 個要儲存的 ask，`max_runs: 3`，之後再依序執行 3 個 | PASS（3 個儲存、5 個 FULL；最後剛好 3 個完整的 run；root 沒有殘留） |
| D-32 | REQ | 2 個 reader 迴圈（`runs`／`trace`／`doctor`）同時面對 24 個 writer | PASS（0 次讀到錯的 run；12 次 NOT_FOUND 是因為 run 在 list 和 trace 之間被淘汰，屬於可接受的 race；沒有把 incomplete 誤判成 complete；沒有卡住） |
| D-33 | REQ | SIGKILL 一個正在儲存的 writer，之後再執行 4 個 ask | PASS（全部成功儲存；abandoned run 在 grace period 內被保留） |
| **D-34** | REQ | 15 次「啟動要儲存的 ask，隨機在 0.05–0.9 秒時送 SIGINT」 | **FAIL**：7/15 次 exit -2，並印出原始 traceback（含 host 路徑）；8/15 次 exit 130。store 保持一致 |
| D-35 | REQ | HTTP provider + TERM／HUP（有儲存） | PASS（依契約調整：預設的 disposition；run 是 incomplete，結果和 F1 相同） |

**D-34 的 coordinator 重現（`$Q/J/D34.*`，指令 `providers`）：**
- 0.03 秒：`Fatal Python error: init_import_site`（Python 直譯器本身）；
- 0.08 和 0.12 秒：`KeyboardInterrupt` traceback，含 `/Users/...` 路徑，rc -2；
- 0.5 秒：rc 0。

**分類：** QA FAILURE — BUG CANDIDATE（Low）。這段時間是 Python 啟動和 import llmtool 模組的期間，`main()` 還沒有開始執行；其中一部分（site import）屬於直譯器本身，無法由 llmtool 修正。可以用「在 entrypoint 外層 catch `KeyboardInterrupt`」縮小這段時間。

## 19. Signal／process lifecycle regression（Bucket D）

- **D-40～D-49（claude-cli 和 codex-cli 各 10 種，共 20 個）：** 全部 PASS。

  | 情境 | 結果 |
  |------|------|
  | 成功 | 0 |
  | TERM | 143 |
  | HUP | 129 |
  | INT | 130 |
  | timeout | 1，`PROVIDER_TIMEOUT` |
  | HUP 被忽略後送 HUP | 0 |
  | INT 被忽略後送 INT | 0 |
  | SIGCHLD 被忽略 | 0 |
  | SIGCHLD 被忽略後送 TERM | 143 |
  | HUP 被忽略後送 TERM | 143 |

  所有情況都沒有殘留的 process（包括孫 process），暫存的 cwd 都被清除。
- **D-50：** 有儲存時送 TERM → 143。lifecycle PASS，run 的狀態問題就是 F1。

## 20. Output／terminal behavior（Bucket B、C）

- **B-70～B-76：** PASS。在真實的 pty 上，ANSI、OSC、C1、bidi、CR、BEL、BS 都會被顯示成可見的 escape，中文、emoji 和 tab 保留；pipe 時保持原始 bytes；`--json` 不會被 stderr 污染；packet 中的 ESC 或 bidi 被拒絕。
- **B-60、B-61、B-62b、B-63、B-64c：** broken pipe 時 exit 0。
- **B-62、B-64／64b／77：** OPTIONAL FAIL，屬於已知限制。
- 沒有看到任何 traceback（D-34 的啟動期間除外）。

## 21. Error-message UX

- **A-44～A-49：** PASS。
  - 沒有 config：`provider base_url is not configured` + `CONFIG_PROVIDER_NOT_READY`；
  - bad config：`malformed YAML` + `CONFIG_INVALID`；
  - credential 未設定：`AUTH_CREDENTIAL_MISSING`；
  - 找不到執行檔：`ENV_PROVIDER_RUNTIME_MISSING`，`doctor` 顯示 `executable=missing`；
  - bad input：`USER_INPUT_ERROR`；
  - 各種邊界數值都安全處理。
- **A-50（internal unexpected error）：** NOT-TESTED。大約 150 次執行都找不到可以從 public 介面觸發的方法；QG-S1 的 DG-2 已用 fault injection 涵蓋。
- **CO-14（REQUIRED）：FAIL → Q-BUG-1**，見 §28。
- **「安全，但不夠 actionable」的觀察（UX，不算 FAIL）：**
  - 沒有 config 時，沒有提示 `llmtool configure` 或 `config path`；
  - config 錯誤沒有指出是哪個 profile 或欄位；
  - `unknown skill` 沒有建議執行 `llmtool skills`；
  - claude 找不到執行檔時沒有安裝提示；
  - permission denied 的 `--file` 顯示「cannot read input as UTF-8 text」；
  - 檔案找不到時只顯示「local I/O failed; details omitted」；
  - `benchmark suite` 的 human 輸出直接印出 Python dict；
  - 被 suppress 的 NEXT_COMMANDS 中，超過 3 個上限的安全指令也被標成「unsafe or unsupported」；
  - TERM／HUP 結束時沒有任何訊息；
  - `doctor` 不會把 abandoned run 或 malformed reservation 顯示為異常。

## 22. Provider QA

| ID | Provider | 方式 | 結果 |
|----|----------|------|------|
| B-33～B-59 | openai-compatible | local mock（正常、各種 HTTP 錯誤、timeout、malformed、沒有 choices 或 content、超大回應、異常的 metadata、`reasoning_content`、enable_thinking 的傳遞） | PASS |
| CO-04～CO-08 | openai-compatible | **live，公司 gateway**（`gw` = qwen3.6-35b-a3b） | PASS |
| CO-12 | openai | `cli.main()` 入口，只把 `API_ORIGIN` 指向 local mock：正常、refusal、incomplete、401、malformed、tool-call；reasoning 和 key 都沒有外洩 | PASS（第一次的 mock 少了 `role: assistant` 欄位，是 mock 的錯誤；修正之後通過） |
| A-51 | openai | 只透過 public CLI 的 end-to-end | NOT-TESTED（origin 是固定的；改由 CO-12 涵蓋） |
| D-40～D-49 | claude-cli、codex-cli | fake runtime | PASS |
| CO-10 | claude-cli | **live，真實的 CLI 2.1.289 + 使用者既有的登入** | PASS（`QA1_CLAUDE_LIVE_OK`，有儲存，沒有殘留暫存目錄） |
| CO-11 | codex-cli | **live，真實的 codex-cli 0.159.2** | PASS（`QA1_CODEX_LIVE_OK`） |
| — | OpenAI API live | 沒有可用的 credential 或 billing | 不在本輪範圍（見 §29 的偏差） |

**live 測試的環境註記：** 第一次的 claude／codex live 測試是 exit 1，原因是我的 `env -i` 沒有設定 `USER`（macOS Keychain 的登入需要它），也沒有設定 `CODEX_HOME`。一般使用者的 shell 都有 `USER`，所以這不是產品的錯誤。但 llmtool 會隱藏 CLI 的 stderr，使用者只會看到「exited with status 1」，很難判斷原因（UX 註記）。

## 23. End-to-end user journeys（coordinator，live 公司 gateway）

| ID | Journey | 步驟 | 結果 |
|----|---------|------|------|
| CO-04 | **A 新使用者** | `config path` → 沒有 config 時 `ask`（錯誤清楚但沒有指引）→ `config init`（0600）→ 複製公司範例 config → `config show` → `providers` → `ask` normal、verbose、debug → `doctor` → 中文和 emoji → stdin → shell loop（加或不加 `</dev/null`） | PASS；沒有建立 store |
| CO-05 | **B DevOps 事故** | 真實感的 app log → `analyze-log`（5 個 section 都正確，root cause 是 DB timeout）→ `context-pack --json`（valid JSON）→ 用 `validate command` 檢查建議的指令（ping、nc、nslookup → UNKNOWN，符合契約）→ `rm -rf`、`cat ~/.aws/credentials` → DESTRUCTIVE、SENSITIVE_READ | PASS |
| CO-06 | **C 開發者 review** | 含有安全 bug 的 diff → `review-diff`（找出 timing attack 和 None role 的提權）→ `retrieve-context`（human 和 `--json`；`.env` 沒有被讀取）→ `orchestrate` 的 grinder → reviewer 流程，以及 `--dry-run`／`--json` | PASS |
| CO-07 | **D 多 provider** | `--provider down`（無法連線）→ active profile 不變 → `LLMTOOL_PROVIDER`；project `.llmtool.yaml` 改 `base_url` 被拒絕，改 `max_tokens` 可以 | PASS |
| CO-08 | **E 失敗後復原** | 慢速 server + `--save-diagnostics` → `PROVIDER_TIMEOUT`、`trace=saved`；慢速 server **只收到 1 次 request**（沒有隱藏的 retry）→ `runs`／`trace --errors` → 手動在公司 gateway 重試成功 → `runs` 同時列出兩個 run | PASS |
| CO-09 | **F 互動 chat** | 公司 gateway，兩輪對話（記住 code word）→ `/exit`；之後沒有任何 diagnostics 狀態 | PASS |
| CO-10、CO-11 | **G 本機 CLI 模型** | 用真實的 claude 和 codex 執行 ask，並儲存 diagnostics | PASS |

## 24. Security／privacy smoke

| ID | Case | 結果 |
|----|------|------|
| CO-13 | synthetic 的 `api_key_env` 值和 inline key：在 `config show`、`doctor`、`ask --debug --save-diagnostics`、`--verbose`、`runs`、`trace --json` 的所有輸出中搜尋 | PASS（key 出現 0 次；inline key 顯示為 `***REDACTED***`；store 裡沒有 key 或 prompt；沒有 traceback） |
| D-05、D-07 | store 裡沒有 9 種 marker；不安全的 store fail closed | PASS |
| B-40～B-58 | 錯誤內容和 reasoning 不顯示 | PASS |
| A-18 | `config show` 的 redaction | PASS |

**NEW SECURITY REVIEW CANDIDATES: 0。** D-34 的 traceback 含有 host 路徑，和 QG-S1 的 DG-2（非預期例外的 traceback）屬於同一類，已有記錄，所以不算新的。

## 25. Cross-environment evidence

| 環境 | 證據 | 結果 |
|------|------|------|
| macOS 15.7.9 x86_64 + Python 3.13.3 | 所有手動 QA，以及完整的 unit suite（928 tests OK，1 skipped） | PASS |
| Python 3.9.6 | 完整的 unit suite（928 OK，1 skipped）；A-09 的 smoke | PASS |
| Python 3.14 | 只有 CI（`37256086556`，Linux） | 本機 NOT-TESTED（CO-17），CI PASS |
| Linux | CI 是自動化的 regression 證據；沒有手動驗收（`renameat2`、XDG 等分支） | NOT-TESTED（CO-15） |
| Windows | 依現行契約不支援 persistence；沒有執行 | NOT-TESTED（CO-16），不列為 FAIL |

## 26. FAIL／BLOCKED／NOT-TESTED cases

| ID | 狀態 | Req | 說明 |
|----|------|-----|------|
| CO-14 | **FAIL** | REQ | Q-BUG-1：錯誤訊息標錯子系統（§28） |
| D-F1 | **FAIL** | REQ | F1：TERM／HUP 時 run 沒有被 finalize（§17） |
| D-34 | **FAIL** | REQ | 啟動期間的 SIGINT 印出 traceback（§18） |
| B-62 | FAIL | OPT | `analyze-log` 小輸出時的 broken pipe → 120（已知限制） |
| B-64／64b／77 | FAIL | OPT | `context-pack`（`--json` 和 human）、`review-diff` 小輸出時的 broken pipe → 120（已知限制；README 只舉例 `analyze-log`） |
| A-50 | NOT-TESTED | REQ | internal unexpected error：沒有 public 的觸發方式 |
| A-51 | NOT-TESTED | ENV | 只透過 public CLI 的 openai end-to-end：origin 是固定的（改由 CO-12 涵蓋） |
| C-716 | NOT-TESTED | OPT | 沒有獨立執行；每個 C-7xx case 都已檢查 |
| CO-15 | NOT-TESTED | ENV | Linux 手動驗收 |
| CO-16 | NOT-TESTED | ENV | Windows（依契約不支援） |
| CO-17 | NOT-TESTED | ENV | 本機的 Python 3.14 |

BLOCKED：0。

## 27. Bug candidates

| ID | 嚴重度 | 內容 | 建議 |
|----|--------|------|------|
| F1 | Low–Medium | TERM／HUP 時 run 沒有被 finalize；`max_runs` 小時最多 1 小時顯示 FULL；`doctor` 沒有提示 | finalize 成 `interrupted`；或寫進 README，並讓 `doctor` 回報 abandoned 的數量 |
| D-34 | Low | 啟動後約 0.2 秒內的 Ctrl-C 印出 traceback 和 host 路徑，exit 是 -2 | 在 `llmtool.py` 和 console entry 外層 catch `KeyboardInterrupt` → 130 |
| B-62 等 | Low（已知） | 非 A11a 指令的小輸出遇到 broken pipe → 120 | 把 B1 的 flush 加上 `silence_broken_pipe` 的模式推廣到 `main()` 的所有指令 |

## 28. Confirmed functional bugs

### Q-BUG-1（CONFIRMED，Low）：錯誤訊息標錯子系統或情境

- **重現（coordinator，`$Q/J/MSG.*`）：**

  | 輸入 | 實際訊息 |
  |------|---------|
  | `orchestrate --dry-run --plan p.json`，`p.json` 是 `{bad json` | `llmtool: feedback JSON is malformed` |
  | plan 有多餘的欄位 | `llmtool: feedback object requires exactly the supported fields` |
  | plan 有 9 個步驟 | `llmtool: feedback collection must be a bounded list or tuple` |

  Bucket C 也觀察到 benchmark 的 spec 和 suite、`validate context` 的錯誤會借用「feedback」或「context packet output」的名稱。
- **相關的情境錯誤：** `ask --provider nope`，以及 `orchestrate`、`retrieve-context` 明確綁定不存在的 profile 時，都顯示 `active provider is not defined`，但使用者並沒有使用 active provider（B-33c、C 的 UX-2）。
- **預期：** 錯誤訊息要指出使用者實際提供的東西（plan、spec、suite、指定的 profile），讓使用者知道要修哪裡。
- **影響：** 使用者會去找根本不存在的 feedback 檔案。exit code 正確，也沒有安全問題。
- **不是 regression**，從 A7、A8、A9 共用的 JSON helper 起就存在。

**CONFIRMED FUNCTIONAL BUG COUNT: 1**

## 29. Known limitations，以及流程上的偏差（如實記錄）

**已知限制（README 已寫明，本輪確認行為一致）：**
- 非 A11a 指令的 broken pipe；
- HTTP provider 的 TERM／HUP 使用預設的 disposition；
- doctor 的 P1–P4；
- M2、M4、M5、N1、TR-3；
- project config 可以選擇 model（不是 model ACL）；
- `runs` 沒有 `--json`；
- abandoned run 的 1 小時 grace period。

**流程偏差：**
1. **Bucket B 的 agent 誤連 api.openai.com 一次。** 它測試 `LLMTOOL_BASE_URL` 時，變數被 `env -i` 清掉，於是送出 **1 次** request 到正式的 OpenAI origin。內容是 prompt「q」和明顯假造的 key `sk-test-not-real`，回應 401，被處理成 `AUTH_PROVIDER_REJECTED`。沒有使用真實的 credential，也沒有送出使用者資料。這次結果**不算**驗收證據。
2. **Bucket A 的 pip 安裝**依照指示用 `--no-build-isolation` 在原始碼目錄 build，因此**更新了已被 gitignore 的 `tools/llmtool/build/` 和 `tools/llmtool/llmtool.egg-info/`**。沒有任何被追蹤的檔案改變。離線環境的 setuptools 67.6.1 低於宣告的 `>=68`，所以從 Python 內建的 test wheel 安裝 setuptools 和 wheel 到 scratch venv。
3. **Bucket A 的 A-52（`HOME` 未設定）** 讓 llmtool fallback 到使用者真正的 `~/.config/llmtool/config.yaml` 路徑。該檔案不存在，也沒有被建立。
4. **coordinator 重現 D-34 時沒有設定 `PYTHONDONTWRITEBYTECODE`**，Python 可能更新了已被 gitignore 的 `tools/llmtool/llmtool/__pycache__/`。沒有任何被追蹤的檔案改變。
5. **Bucket C 第一次的 Ctrl-C 測試**因為 harness 沒有把 pty 設成 controlling terminal 而無效；修正 harness 之後重跑，結果有效（C-104 PASS）。
6. **mock 錯誤（CO-12）：** 第一次的 openai mock 少了 `role: assistant` 欄位，導致誤判；修正後重跑。
7. 所有 agent 都只結束自己啟動的 PID（mock server、fake CLI），沒有用 pattern kill。沒有用 `rm` 刪除任何東西，所以 scratchpad 的檔案都保留。結束時 `/private/tmp/llmtool-*` 只剩先前已知的 3 個目錄。
8. 這一輪沒有修改 production code、tests 或文件。repo 唯一的新增是這份報告。

## 30. Final release-quality assessment

- **作為日常工具的品質：好。** 主要流程（設定、問答、事故分析、review、多 profile、失敗復原、chat、本機 CLI 模型）在 live 環境都能完成。輸入的處理嚴謹，錯誤安全、不會外洩，diagnostics 在並發和中斷下也保持一致，DG-1 沒有退化。
- **依照本輪的定義是 FAIL**，因為有 3 個 REQUIRED case 失敗：Q-BUG-1（錯誤訊息）、F1（TERM／HUP 的 diagnostics 狀態）、D-34（啟動期間的 Ctrl-C traceback）。它們都是 Low 或 Low–Medium，不影響主要的結果，沒有資料損壞，也沒有流程完全壞掉。
- **建議 coordinator triage：**
  1. 修 Q-BUG-1，並決定 F1（finalize，或寫進文件）和 D-34（在 entrypoint catch）；
  2. 順便決定是否要統一非 A11a 指令的 broken pipe 行為；
  3. 修正之後重跑受影響的 case（CO-14、D-F1、D-34、B-62 等），再重新判定 QG-QA1。
- A11b 依原計畫，等 QG-QA1 通過之後才開始。

```text
QA CASES TOTAL: 271
PASS: 260
FAIL: 5
BLOCKED: 0
NOT-TESTED: 6
CONFIRMED FUNCTIONAL BUG COUNT: 1
NEW SECURITY REVIEW CANDIDATES: 0
QG-QA1 RESULT: FAIL
READY FOR COORDINATOR TRIAGE: YES
```
