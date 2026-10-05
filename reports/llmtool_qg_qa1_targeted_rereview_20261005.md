# llmtool QG-QA1 Targeted Re-QA / Independent Verification — PR #31（2026-10-05）

## 1. Executive summary

這一輪獨立驗證 PR #31 是否真的修好了上一輪 [QG-QA1](llmtool_qg_qa1_formal_qa_20261005.md) 的 3 個 REQUIRED findings，並且沒有破壞相鄰的功能。**我沒有把 Codex 的 validation report 當成證據**，所有結論都來自我自己讀 source 和執行 public-interface probe。

- **F1：PASS。**
  - claude-cli 和 codex-cli 收到 TERM／HUP 時：rc 143／129，process tree 和 cwd 都被清除，run 被 finalize 成 `interrupted`、trace 以 `run.end` 結尾、沒有殘留的 artifact。
  - `max_runs=1` 時，下一次儲存立即成功；`doctor` 正常；store 裡沒有 marker。
  - 不儲存或 store 不安全時，exit code 保持不變；child 自己 exit 143，或任意的 `SystemExit(143)`，都**不會**被當成 host termination 來 finalize。
- **D-34b：PASS。** lazy import `llmtool.cli`、parser construction、`parse_args` 三個階段，在 3 種 launcher 上，無論是注入 exception 或真實的 SIGINT，都是 rc 130、固定的 stderr、沒有 traceback 或 host 路徑。entrypoint 只 catch `KeyboardInterrupt`。
- **Q-BUG-1：FAIL（只修好一部分）。**
  - JSON parse 和 schema 的錯誤訊息都已改成正確的領域，明確指定的 profile 也改成 requested 的措辭，所以**原本報告的 3 個例子都已修正**。
  - 但是**檔案層級的錯誤**仍然顯示 `feedback file ...`：`--plan`、`--spec`、`--case`、`--manifest`、`--runtime-file`、`--output-file`、suite entry 的檔案過大、不是 UTF-8、找不到、traversal 等。
  - `benchmark` 的輸出含有控制字元時，訊息仍然是 `feedback text contains unsupported controls`。
  - 這屬於同一個 Q-BUG-1（錯誤訊息標錯領域），**不是新的 bug，也不是 PR #31 造成的**，但是這次沒有被修好。
- **相鄰的 regression 全部 PASS：** DG-1、launcher、主要的使用者流程、並發、隱私，以及 parser 的嚴格度（只改了標籤，驗證的語意沒有改變）。

```text
Q-BUG-1: FAIL
F1: PASS
D-34b: PASS
D-34a: PLATFORM LIMITATION
DG-1 ADJACENT REGRESSION: PASS
LAUNCHER REGRESSION: PASS
KEY USER JOURNEYS: PASS
NEW CONFIRMED FUNCTIONAL BUGS: 0
NEW SECURITY REVIEW CANDIDATES: 0
TARGETED RE-QA RESULT: FAIL
PR #31 MERGE RECOMMENDATION: REQUEST CHANGES
READY FOR COORDINATOR FINAL REVIEW: YES
```

**NEW CONFIRMED FUNCTIONAL BUGS 是 0 的原因：** Q-BUG-1 剩下的部分（R-1）是原本 confirmed bug 尚未修好的部分，不算新的 bug。

## 2. Exact base／PR HEAD（實際的 remote 狀態）

| 項目 | 值 |
|------|-----|
| `origin/main` | `fe169aff967f4e7329128cf434c93ff3a3002b30`（和預期相同） |
| PR #31 的 remote HEAD | `b2a30a72da968c930d812e8cb6b5f5c343ca2ec6`（和預期相同；PR 是 Draft） |
| implementation commit | `805caaa`（之後的 `b2a30a7` 只修改報告） |
| merge-base／drift | merge-base = `fe169af`；ahead 4、behind 0，**沒有 base drift** |
| working tree | 被追蹤的檔案是乾淨的；未追蹤的只有使用者原本就有的 6 個 reports 項目和 3 個 `tools/llmtool/case.*` |

## 3. Environment

- macOS 15.7.9 x86_64。
- Python 3.13.3（`tools/llmtool/.venv`）和 3.9.6（`/usr/bin/python3`）。3.14 只透過 CI。
- 所有 probe 都在 scratchpad `$Q=/private/tmp/claude-501/-Users-davidsun-git-davidcataiporject-davidcattoolsproject/88ee90aa-8e62-4eeb-a67b-e373aeba2609/scratchpad/reqa/` 內各自用 `mktemp -d` 建立，並以 scratchpad 為工作目錄執行；使用 `env -i`、獨立的 `HOME`／`LLMTOOL_HOME`。
- provider 使用會計算 request 數的 local mock，以及 fake claude／codex（慢速模式會 fork 一個 sleep 孫 process，並記錄它的 PID 和 cwd）。
- **公司 gateway 這一輪無法連線**（curl 得到 000，llmtool 回報 `PROVIDER_TIMEOUT`），所以 live smoke 是 BLOCKED。live 不是 REQUIRED，也沒有使用真實的 Claude／Codex token。

## 4. Independent QA strategy

1. 閱讀上一輪的 QG-QA1 報告、PR #31 的 validation report（只用來了解它聲稱修了什麼）、QG-S1、DG-1 的三份報告、README、ROADMAP、WORKFLOW、`skills/qa-validation`、`skills/bug-hunt`，以及 **PR #31 的完整 diff**。
2. **從 source 看到的修正方式：**
   - `entrypoint.py`：lazy import，只 catch `KeyboardInterrupt` → 130；
   - `cli.main()`：把 `parse_args` 移到 try 裡面；
   - 新增 `CLIHostTermination(SystemExit)`，只由 `CLIProcessLifecycle.__exit__` 在 TERM／HUP 時 raise；`_run_ask` 對它 `finish('interrupted')` 之後再 re-raise；
   - feedback 的 validator 加上 `context` 標籤；
   - `config` 對 `--provider` 和 `LLMTOOL_PROVIDER` 改成 requested 的措辭；
   - `pyproject` 的 console script 改指向 `llmtool.entrypoint:main`。
3. 依照 coordinator 給的矩陣執行，**並且自行加了以下 case**：
   - CLI child 自己 exit 143（CHILD143）；
   - 自訂 SIGCHLD handler；
   - 在 launcher import 期間送 SIGINT（Y-08）；
   - 檔案層級的錯誤標籤（因此找到 R-1）；
   - 並發測試 3 種啟動順序 × 3 輪。
4. 分工：
   - coordinator：F1、相鄰的 signal regression、HTTP control、DG-1、任意 `SystemExit` 的 control、CI；
   - 3 個 QA agent：（X）Q-BUG-1 和嚴格度、（Y）D-34b、launcher 和 SystemExit 的邊界、（Z）使用者流程、並發和隱私。
   - **R-1 由 coordinator 親自重現。**

## 5. Q-BUG-1 results

| 群組 | Req | 結果 |
|------|-----|------|
| feedback 的對照組（malformed、extra、missing、duplicate、深度、控制字元、常數、文字上限、不是 UTF-8、超過上限、找不到檔案） | REQ | **PASS**：仍然是 `feedback ...`，A7 沒有被破壞 |
| orchestration：malformed JSON、多餘的欄位、malformed step（extra、missing、不是 object）、9 個步驟、duplicate key、深度 7 或 500、raw 控制字元、NaN、不合法的 role 或 provider、剛好 1 MiB 可以接受 | REQ | **PASS**：`orchestration plan …`、`orchestration plan step …`、`orchestration role is unsupported`；沒有 echo marker；沒有 traceback 或路徑；request 為 0 |
| **orchestration：plan 超過 1 MiB、不是 UTF-8、找不到、是目錄、traversal** | REQ | **FAIL → R-1**：`feedback file exceeds 1 MiB`、`feedback file must be UTF-8`、`feedback file operation failed; details omitted`、`feedback file paths cannot contain parent traversal` |
| benchmark：spec、case、suite、runtime 的 malformed、schema、duplicate、深度、常數、控制字元；spec 內的 regression schema 錯誤；16／17 個 tag；32／33 個 entry | REQ | **PASS**：`benchmark spec …`、`benchmark case …`、`benchmark regression …`、`benchmark suite …`、`benchmark runtime …`、`benchmark …` |
| **benchmark：spec、case、manifest、runtime、output、entry 檔案層級的錯誤；輸出含有 ESC** | REQ | **FAIL → R-1**：`feedback file …`、`feedback text contains unsupported controls` |
| 明確指定的 profile：`ask --provider missing`、`LLMTOOL_PROVIDER=missing`、`benchmark live` 的 CLI 或 env、orchestration 的 step 1 或 step 2、retrieval 缺少 planner 或 synthesizer | REQ | **PASS**：`requested provider profile is not defined`，request 為 0（step 2 的情況是 step 1 先執行了 1 次 request，符合「每一步執行前才 resolve」的契約，見 E-1） |
| 真正的 active provider 不存在（`active_provider: missing`，沒有 override） | REQ | **PASS**：`active provider is not defined in user config or defaults` |
| coordinator 抽測（`$Q/R1.*`、`$Q/QB1F.*`） | REQ | 同上：parse、schema、profile 都 PASS；**檔案層級的錯誤 FAIL**（親自重現 orchestrate 的 oversize、bad UTF-8、找不到檔案，以及 benchmark spec、suite 找不到檔案） |

## 6. Strict parser regression results

- **feedback、benchmark、orchestration 抽測**：duplicate key、`MAX_DEPTH=6`（6 可以解析，7 被拒絕）、JSON 和檔案的 1 MiB 上限（剛好 1 MiB 接受，多 1 byte 拒絕）、欄位必須完全一致、控制字元被拒絕、malformed UTF-8、item 上限（64／65 個 assertion、16／17 個 tag、32／33 個 suite entry、8／9 個步驟）。**全部和修正前相同。**
- 用 `git show fe169af:<path>` 比對修正前的程式碼：每個檢查、上限和分支都沒有改變，只多了 `context` 標籤；`parse_regression_case` 新增的 try／except 只改寫 prefix 再 re-raise，不會吞掉錯誤。
- **結論：PASS**，validation 的語意沒有改變。

## 7. F1 TERM／HUP results（coordinator，`$Q/F1c.*/result.txt`）

設定：`persist_metadata: true`、`max_runs: 1`，fake 在慢速模式下會 fork 一個 `sleep 3000` 孫 process，並記錄 PID 和 cwd。

| Case | rc | 孤兒 process | cwd 殘留 | store | 下一次儲存（`max_runs=1`） | 隱私 |
|------|----|-------------|---------|-------|---------------------------|------|
| claude TERM | 143 | 0 | 無 | `interrupted`，最後一筆是 `run.end`，只有 `.run.lock`、`summary.json`、`trace.jsonl` | rc 0、`FAST_OK`、`persisted=true`、沒有 FULL；`runs` 沒有 incomplete；`doctor` 顯示 `run_count=1 active_count=0` | 沒有 marker |
| claude HUP | 129 | 0 | 無 | 同上 | 同上 | 沒有 marker |
| codex TERM | 143 | 0 | 無 | 同上 | 同上 | 沒有 marker |
| codex HUP | 129 | 0 | 無 | 同上 | 同上 | 沒有 marker |

- 搜尋的 marker 包括 prompt（`QA_PROMPT_MARK`）、回答（`FAST_OK`）、CLI 的 stderr secret（`QA_FAKE_STDERR_SECRET_MARK`）和 `/Users/`。
- verbose 的 stderr 顯示 `diagnostics_persisted=true`。
- Bucket Z 的 jC／jK／jS 也獨立確認了同樣的行為（Z-03～Z-05）：`runs` 顯示 `recorded_status=interrupted`，trace 是 complete。

**F1：PASS。**

## 8. Signal lifecycle adjacent regression

| Case | 結果 |
|------|------|
| claude 和 codex 的 SIGINT（有儲存） | PASS：rc 130，`interrupted`，durable，process tree 乾淨，下一次可以儲存 |
| 不儲存時的 TERM／HUP（claude、codex） | PASS：rc 143／129，**不會建立 store**，沒有孤兒 process |
| store 不安全（0755）時的 TERM／HUP | PASS：rc 143／129 保持不變，只有一筆 `DIAGNOSTICS_STORE_UNSAFE` warning，沒有 traceback，store 沒有被 chmod |
| **CLI child 自己 exit 143**（自行新增） | PASS：rc 1，`Claude/Codex CLI exited with status 143`，`PROVIDER_RUNTIME_ERROR`，run 是 `error`；**沒有被當成 host termination** |
| **任意的 `SystemExit(143)`**（HTTP provider 的 `complete` 被 patch 成 `raise SystemExit(143)`，透過 `entrypoint.main`） | PASS：rc 143，run **沒有被 finalize**（只有 `trace.partial.jsonl`）；只有 `CLIHostTermination` 會觸發 finalize |
| DG-1：claude 和 codex 的 timeout（`PROVIDER_TIMEOUT`，沒有孤兒，cwd 被清除）、成功 | PASS |
| SIGHUP=`SIG_IGN` + HUP、SIGINT=`SIG_IGN` + INT | PASS：兩個 provider 都 rc 0，正常完成 |
| SIGCHLD=`SIG_IGN` | PASS：rc 0，可以正常執行 |
| **自訂的 SIGCHLD handler** | PASS：`CLI provider cannot use a custom child reaper`，`PROVIDER_RUNTIME_ERROR`（fail closed） |

## 9. HTTP control

慢速的 local HTTP server，有儲存的 `ask`，執行中送出：

| Signal | 結果 |
|--------|------|
| TERM | rc -15，stderr 是空的，run 是 incomplete（`.reservation`、`.run.lock`、`trace.partial.jsonl`） |
| HUP | rc -1，同上 |

HTTP provider 維持 OS 的預設 disposition，**沒有**被納入 CLI lifecycle，和 README 新增的「HTTP TERM/HUP retains the default OS disposition」一致。**PASS。**

## 10. D-34a／D-34b boundary（Bucket Y）

| 類別 | 證據 | 判定 |
|------|------|------|
| **D-34a**：SIGINT 到達時 Python 還沒安裝 handler（直接 rc -2），或正在進行 site、`.pth`、editable finder、Homebrew sitecustomize、zipimport 的初始化 | 3 種 launcher 共 264+70 次延遲 sweep；大約 28–50 ms 出現 `Fatal Python error: init_import_site` 或 site 相關的 frame | **PLATFORM LIMITATION**（不需要 PR #31 修正） |
| **D-34b**：(a) lazy import `llmtool.cli`、(b) parser construction、(c) `parse_args` | 注入 exception（Y-03～Y-05）：3／3；**真實的 SIGINT 搭配 readiness 同步**：direct 和 module launcher 54／54、console script 27／27，全部 rc 130、stderr 正好是 `llmtool: interrupted\n`、stdout 是空的、沒有 traceback、HOME 是空的 | **PASS** |
| 執行中的 ask 遇到 SIGINT（慢速 mock，3 種 launcher × 3 次） | 9／9：rc 130，固定的 stderr | PASS |
| **Y-F1（自行新增）：launcher 自己的 `from llmtool.entrypoint import main` 那一行** | 在這一行暫停時送 SIGINT：traceback、rc -2；direct 和 module launcher 顯示 host 路徑。自然命中率大約 1/70～1/260，只出現在 45–48 ms 附近幾 ms 的範圍 | **NON-BLOCKING UX**（不是 PR #31 造成的；PR 把原本涵蓋整個 `llmtool.cli` import 的範圍，縮小到 import 兩個很小的模組。setuptools 產生的 console script 無法修正這點；`llmtool.py` 和 `__main__.py` 可以選擇在外層包住 import） |

## 11. Launcher／entrypoint results（Bucket Y）

| Case | 結果 |
|------|------|
| scratch venv 的 console script：`pip install --no-index --no-deps --no-build-isolation`；script 的內容是 `from llmtool.entrypoint import main`，`entry_points.txt` 是 `llmtool = llmtool.entrypoint:main`；安裝後的 `entrypoint.py` 和 repo 的完全相同 | PASS |
| 3 種 launcher（console、`python -m llmtool`、`tools/llmtool/llmtool.py`）：`--help` 0、`--version` 0、invalid command 2、`--nope` 2、`providers` 0、`providers --json` 0、`runs` 0、沒有設定時 `ask hi` 1 | PASS（24／24 結果相同；沒有 traceback 或 `/Users`；HOME 前後的快照不變） |
| SystemExit 的邊界：argparse usage → 2；`SystemExit(0)`／`None` → 0；`SystemExit(7)` → 7；`CLIHostTermination(143／129)`（直接 raise，以及經過 `_run_ask` 的 dispatch）→ 143／129，**不會變成 130** | PASS |

## 12. Key user journeys（Bucket Z，mock 與 fake）

| Journey | 結果 |
|---------|------|
| **A 新使用者**：config path → init → 寫入 profile → show（key 被遮蔽）→ ask → verbose、debug → `--save-diagnostics` → runs、trace、`trace --json`、`--errors` → doctor | PASS：opt-in 之前沒有 store；2 個儲存的 run 都 complete；5 次 ask 剛好 5 次 request；store 和 stderr 都乾淨 |
| **E 失敗後復原**：HTTP 500 和 2 秒 timeout，都有儲存 → runs、trace → 手動重試 | PASS：`PROVIDER_HTTP_ERROR`（http_status 500）、`PROVIDER_TIMEOUT`，兩者都是 `trace=saved`；重試成功；**每個指令剛好 1 次 request**；錯誤內容沒有外洩 |
| **CLI provider**：claude 和 codex × TERM、HUP，`max_runs=1` | PASS（同 §7） |
| live 公司 gateway（OPTIONAL） | **BLOCKED**：這一輪 gateway 無法連線（`PROVIDER_TIMEOUT`，curl 得到 000）；llmtool 正確儲存了 error trace |

## 13. Concurrency adjacency（Bucket Z，Z-04）

- **設定：** `max_runs: 2`，3 輪，每輪使用不同的啟動順序，全部共用同一個 store。每一輪是：2 個 mock ask、1 個慢速 claude（中途送 SIGTERM）、1 個慢速 codex（中途送 SIGHUP），之後再依序執行 3 個要儲存的 ask。
- **結果：PASS（3／3）。**
  - `DIAGNOSTICS_STORE_FULL` 只在 2 個名額都被執行中的 run 佔用時才出現；這時 stdout 仍然正確，rc 是 0／143／129，warning 只出現在 stderr。
  - 9 個依序執行的 ask **全部**立即儲存成功，而且是 complete。
  - 有拿到名額的 interrupted run 都是 complete／interrupted；recorded status 和 trace state 從未矛盾。
  - 每一輪結束時都剛好是最後 2 個 run，`doctor` 顯示 `run_count=2 active_count=0 unknown_count=0`。
  - 每個指令 1 次 request；沒有卡住（每個都在 2 秒內結束）；沒有孤兒 process。

## 14. Security／privacy smoke（Bucket Z，Z-05，加上 coordinator 的 §7）

- 在 claude、codex × TERM、HUP × debug、verbose 的情況下，prompt、回答、CLI stderr、錯誤內容、secret、`/Users/`、scratch 路徑、`Traceback` 在 store 和所有輸出中都是 0 筆。
- 權限是 0700／0600。
- child 的環境只有 HOME、LANG、PATH（codex 另有 TMPDIR），不含 secret 變數；child 的 cwd 是 `/private/tmp/llmtool-*`，結束後被清除。
- 每個指令 1 次 request，沒有 retry 或 fallback；scratch git repo 的快照不變，沒有 shell、Git 或檔案的修改。
- **NEW SECURITY REVIEW CANDIDATES: 0。** trace 會記錄 profile 名稱，這是既有的行為，reader 不會顯示它。

## 15. CI evidence

| Run | head | 結果 |
|-----|------|------|
| push `37264861817` | `b2a30a7` | success。`check (3.9)` 和 `check (3.14)` 都是 success，其中 **Offline canonical checks=success**、**Working tree is clean=success** |
| pull_request `37264912362` | `b2a30a7` | success |
| 本機完整測試 | `b2a30a7` | Python 3.13.3：**965 tests OK**（1 skipped）；Python 3.9.6：**965 tests OK**（1 skipped） |

CI 不能取代上面的 public QA。

## 16. New findings

| ID | 分類 | 嚴重度／信心 | 說明 | PR #31 造成的？ | Merge blocker？ |
|----|------|-------------|------|----------------|-----------------|
| **R-1** | **Q-BUG-1 尚未修好的部分**（原本 confirmed functional bug 的一部分，不算新的 bug） | Low／High | 所有 orchestrate、benchmark（evaluate、live、suite、runtime、output、suite entry）的檔案讀取都經過 `feedback_files.read_feedback_text`，它固定使用 `feedback file …` 的訊息。另外，`benchmark` 的輸出含有控制字元時，`evaluate_regression` 的 `_text` 會顯示 `feedback text contains unsupported controls`。**重現：** `python3 -c 'open("big.json","w").write("{\"steps\":[]}"+" "*(1024*1024))'`，然後執行 `llmtool orchestrate --dry-run --plan big.json`，得到 `llmtool: feedback file exceeds 1 MiB`、rc 1、request 為 0。因為檔案上限和 JSON 上限相同，過大的 plan 只會出現這個訊息。拒絕行為和 exit code 都正確，沒有外洩 | 否（原本就存在，這次沒有修） | **YES**（要宣告 Q-BUG-1 已修好的話）。如果 coordinator 決定把它拆成獨立追蹤的 follow-up，則可以是 NO |
| Y-F1 | NON-BLOCKING UX | Low／High | 在 launcher 自己的 import 那一行送 SIGINT 仍會印出 traceback（§10） | 否（範圍反而縮小了） | NO |
| E-1 | EXPECTED | — | orchestration 的 step 2 是不存在的 profile 時，step 1 會先執行 1 次 request；符合「每一步執行前才 resolve」的契約 | 否 | NO |
| N-1 | NON-BLOCKING UX | Low | `--case` 的 parse 錯誤寫 "benchmark case"，schema 錯誤寫 "benchmark regression"；runtime、safety、suite entry 的 schema 錯誤只寫 "benchmark object"，不夠具體 | 引入了新的標籤，但不是錯誤 | NO |
| N-2 | NON-BLOCKING UX | Low | TERM／HUP finalize 之後，沒有 `--verbose` 時 stderr 是空的，使用者拿不到 run_id，只能從 `runs` 查到 | 否（之前就沒有輸出） | NO |

## 17. Remaining limitations

- D-34a（直譯器和 site 的初始化）是 platform limitation；Y-F1 剩下的 launcher import 範圍。
- 非 A11a 指令的 broken pipe 是 OPTIONAL 的已知限制；HTTP 的 TERM／HUP 維持預設行為。
- DG-2、DG-3、TR-1～TR-3、M2、M4、M5、doctor 的 P1–P4、N1 等延後項目都沒有改變。
- **流程記錄：**
  - **公司 gateway** 這一輪無法連線，所以 live smoke 是 BLOCKED。
  - **我自己 driver 的兩次失誤，都已修正後重跑，結果無效的那兩次不採用：**
    1. 用 `open(cfg,'w')` 時先把檔案清空才讀取，導致 config 變成空的，所有 case 都是 `CONFIG_INVALID`；
    2. fake CLI 依賴環境變數，但 llmtool 傳給 child 的環境是 allowlist，所以 fake 收不到。這次留下一組 llmtool、fake、sleep process，我確認它們的 PID 之後，對 llmtool 送 TERM 結束（順帶驗證了 F1 的清理行為），之後確認沒有任何殘留。
  - **Bucket Y 的 console-script 安裝**和上一輪一樣，更新了已被 gitignore 的 `tools/llmtool/build/` 和 `llmtool.egg-info`（現在的 entry point 是 `llmtool.entrypoint:main`）；沒有任何被追蹤的檔案改變。
  - 所有 agent 都只結束自己記錄的 PID；結束時沒有殘留的 probe process；沒有用 `rm` 刪除任何東西。
- 沒有測試：Linux 手動驗收、Windows、本機的 Python 3.14、真實的 Claude／Codex／OpenAI live。

## 18. Merge recommendation

- **F1、D-34b、DG-1 regression、launcher、使用者流程、並發、隱私和 CI 都通過**，這個 PR 沒有帶來任何 regression。
- **但 Q-BUG-1 只修好一部分**：上一輪報告的 JSON、schema 和 profile 的例子都已修正，可是同一類「錯誤訊息標錯領域」的問題仍出現在所有 orchestrate 和 benchmark 的**檔案層級錯誤**，以及 benchmark 輸出的控制字元錯誤（R-1）。
- **建議：REQUEST CHANGES**，請擇一處理：
  1. 讓 `read_feedback_text`（以及 `evaluate_regression` 的 `_text`）接受 caller 傳入的 domain 標籤，orchestrate 和 benchmark 改為傳入自己的標籤，並為 oversize、bad UTF-8、missing、traversal、輸出控制字元補上 public CLI 的 regression 測試；之後只需要重跑 §5 標成 FAIL 的那幾列。
  2. 或者，由 coordinator 明確把 R-1 拆成獨立追蹤的 follow-up，並接受 Q-BUG-1「部分修正」。在這種情況下，F1 和 D-34b 的部分可以視為通過。
- A11b 仍然未開始；這一輪沒有 merge，PR #31 保持 Draft。

```text
Q-BUG-1: FAIL
F1: PASS
D-34b: PASS
D-34a: PLATFORM LIMITATION
DG-1 ADJACENT REGRESSION: PASS
LAUNCHER REGRESSION: PASS
KEY USER JOURNEYS: PASS
NEW CONFIRMED FUNCTIONAL BUGS: 0
NEW SECURITY REVIEW CANDIDATES: 0
TARGETED RE-QA RESULT: FAIL
PR #31 MERGE RECOMMENDATION: REQUEST CHANGES
READY FOR COORDINATOR FINAL REVIEW: YES
```
