# llmtool 程式碼檢查、除錯與改善建議報告

- 日期：2026-10-24（Asia/Taipei）
- 檢查者：Claude（Owner 直接開的 Claude Code session，唯讀 Reviewer 角色；本報告不改任何程式碼）
- 檢查版本：main `cf6eb6d92100dc684d49f1f28434d4fd053f3eb6`（與 origin/main 相同）
- 範圍：`tools/llmtool`（程式約 2.6 萬行、測試）、`scripts/llmtool_monitor.py`、`scripts/install-skills.sh`、CI workflow、skills 與文件大小。**nlf 依 Owner 指示不在本輪範圍。**
- 與 [#313](https://github.com/davidcatcat/davidcattoolsproject/pull/313) 的分工：#313 看的是營運面（PM 接續、閒置 session、審查呼叫、D6 堆積）；本報告看的是**程式碼層的 bug**，每一項都附可重跑的重現步驟。兩份不重複。

## 1. 結論

- **main 現在是紅的**：`cf6eb6d`（#311，只改 ROADMAP 一行）的 push CI 在 Python 3.14 失敗 1 個測試。#313 寫的「main 健康」已經過時。失敗不是 #311 造成的，而是下面 P1-1 的並發 bug 讓測試偶發失敗。
- **P1-1 TaskStore 的鎖設計有兩個真實 bug**（已重現）：
  - 寫入中途當機會留下 lock 檔，之後這個任務**永遠無法更新**；
  - 兩個寫入者同時進來時不會等待、直接失敗。main CI 那次失敗裡，兩個派工**都**失敗：一次性 claim 已經用掉，provider 卻沒有被呼叫。
- **P2 company gw 11/11 失敗的可能根因**：輸出驗證器要求第一個字元就是 `diff --git`，hunk 行數也要完全正確。LLM 常見的輸出格式偏差全部會被拒。被拒的種類剛好對應 #290 的 ENVELOPE／DIFF 各半。
- **P2 監控顯示的派工開關可能與真正的閘門不一致**：派工檔有任何一處不合規時，llmtool 會拒絕**全部**派工，監控卻仍顯示 ALLOWED。
- 其他：錯誤碼被吞掉，導致 flake 無法從 log 判讀；監控的 GitHub 動態漏掉約六成留言；文件太大，耗費 token；有 CI 成本可以省。見 §3、§4。

## 2. 驗證紀錄

| 項目 | 結果 |
|---|---|
| main CI（GitHub，`cf6eb6d` push） | 3.9 success；**3.14 failure**：2095 tests、1 failure（`test_concurrent_stale_delegations_claim_once_before_provider_call`） |
| 監控測試（本機 Mac，Python 3.13.3） | `python3 -B -m unittest discover -s scripts`：108 tests OK |
| llmtool 全套（本機 Mac，Python 3.13.3 venv） | 2095 tests OK（skipped 3），見 §2.2 |
| 靜態檢查 | pyflakes：0 個未定義名稱，9 個未使用 import；vulture（≥80%）：只有簽章參數與 1 處未使用參數 |
| 重現腳本 | 在本機 scratch 目錄執行；不寫入 repo，也不碰真實 TaskStore（`~/.local/state/llmtool`） |

### 2.1 本機全套測試

第一次跑時用的是系統 Python，沒有裝 PyYAML 和 llmtool，結果出現 684 failures、167 errors。**這是我自己的環境設定錯誤，不是產品問題。**之後在 scratch venv 裝了 `requirements-ci.txt`，再重跑一次，結果見 §2.2。

### 2.2 venv 重跑結果

- 環境：本機 Mac，Python 3.13.3，scratch venv 裝 `PyYAML==6.0.3`、`prompt-toolkit==3.0.52`、`wcwidth==0.9.2`，用 `.pth` 指到 worktree 的 `tools/llmtool`，不寫入 repo。
- 指令：`python -B -m unittest discover -s tests`
- 結果：**Ran 2095 tests in 896s — OK (skipped=3)**，測試數和 CI 一致。
- P1-1 的並發 flake 在本機沒有出現，和 §3 的時序分析相符。本機通過不代表沒有 bug。

## 3. 發現的問題（依嚴重度排序）

### P1-1 TaskStore 的 lock 不會等待，也不會回收（SEV-2，已重現）

**位置**：[`task_store.py` `_update`](../tools/llmtool/llmtool/task_store.py)（約 1393–1404 行）

**做法**：用 `os.open(lock, O_CREAT|O_EXCL)` 建一個 lock 檔，結束時刪掉。拿不到鎖就立刻失敗：沒有 `expected` 時報 `TASK_STORE_BUSY`，有 `expected` 時報 `TaskStoreConflict`。

**Bug A：當機留下的 lock 會永久卡住任務**
- 重現方式：子程序拿到鎖後被 `SIGKILL`。之後同一任務的 `update` 和 `compare_and_update` 每次都失敗：
  ```
  leftover files: ['.<task>.json.lock', '<task>.json']
  update 0 FAILED: LLMToolError task store unavailable; details omitted
  update 1 FAILED: LLMToolError task store unavailable; details omitted
  compare_and_update FAILED: TaskStoreConflict - developer assignment already exists; duplicate dispatch refused
  ```
- 程式裡沒有任何處理過期 lock 的邏輯。最後那則訊息還會誤導 PM，以為是「重複派工」。
- 會觸發的情況：Mac 睡眠或關機、`kill -9`、OOM、Codex 或 Claude CLI 被強制結束。

**Bug B：並發寫入互相打掉（main CI 紅燈的原因）**
- 證據：GitHub run `cf6eb6d` / Python 3.14：
  ```
  AssertionError: 0 != 1 : [LLMToolError('task store unavailable; details omitted'),
                            LLMToolError('developer assignment already exists; duplicate dispatch refused')]
  ```
- 機制（對照 [`run_task_delegate`](../tools/llmtool/llmtool/task_submission_cli.py) 約 1114–1160 行）：
  1. 贏家的 CAS claim 成功；
  2. 輸家的 CAS 拿到鎖、讀檔、發現不符，準備報 conflict。這段持鎖只有幾毫秒；
  3. 剛好在這段時間內，贏家呼叫 `store.update(developer=DISPATCHING)`，沒有等待，直接拿到 `TASK_STORE_BUSY`；
  4. 這個錯誤不是 `WorkerDispatchDenied`，所以沒被捕捉，整個指令中止。
- 結果：claim 已經持久化成 `DEVELOPER_PENDING`，provider 卻沒有被呼叫，兩邊都失敗。
- 本機 40 次、加 CPU 壓力 40 次都沒重現，屬於時序型 flake。CI 的 3.14 runner 比較慢，所以會撞到。

**對照**：同一個 repo 的 worker availability store、receipt store 和 diagnostics reader 都已經改用 `fcntl.flock`。只有 TaskStore 還在用 lock 檔。

**建議修法**（最小、可逆）：
- 鎖改用 `fcntl.flock(LOCK_EX)`，加上有上限的等待（可沿用 diagnostics reader 的 2 秒 deadline）。程序結束時 kernel 會自動釋放，Bug A 就消失；
- 等待逾時才報 `TASK_STORE_BUSY`，Bug B 就消失；
- CAS 比對不符時，仍然報 `TaskStoreConflict`；
- 鎖檔可以固定保留，不要每次刪除。刪除再重建會讓 flock 失去互斥。

**還要補的測試**：
- 持鎖的子程序被 SIGKILL 之後，任務仍可更新；
- 用 barrier 精準製造「輸家持鎖時贏家寫入」的情況，期待贏家等待後成功。

**處置**：交給 Developer 修。修好前，main CI 的 3.14 失敗可以先 rerun 判斷，但**不應該把這當成單純的 flake 結案**。

### P1-2 main CI 紅燈，PR #312 CI 也失敗（營運）

- main `cf6eb6d`：原因見 P1-1。
- PR [#312](https://github.com/davidcatcat/davidcattoolsproject/pull/312)：在「Source revision」步驟失敗，原因是分支沒有包含目前的 base。需要先同步 main，再重跑 CI。這不是程式碼問題。

### P2-1 TaskStore 把錯誤碼丟掉了（SEV-3，可除錯性）

- [`task_store._fail(code)`](../tools/llmtool/llmtool/task_store.py)（約 117 行）完全不用 `code` 參數。`TASK_STORE_INVALID`、`BUSY`、`UNAVAILABLE`、`CONFLICT`、`WORKER_DISPATCH_AUDIT_LIMIT` 最後都變成同一句「task store unavailable; details omitted」。
- 影響：上面的 CI flake 只看 log 分不出是 BUSY 還是資料損壞。PM 和 Developer 只能猜，或花 token 重跑。
- 建議：把 code 掛到例外物件上，例如 `LLMToolError` 的 code 屬性，或照 diagnostics 已有的 coded error 做法。訊息仍然不含路徑或內容，符合隱私規則。

### P2-2 company gw 的 Developer 輸出幾乎一定被拒（SEV-3，功能，對應 #290）

**位置**：[`_developer_output_rejection` / `_patch_paths`](../tools/llmtool/llmtool/task_submission_cli.py)（約 398–495 行）

**實測**：只呼叫本地驗證函式，不呼叫任何模型。

| 輸出樣式（LLM 常見） | 結果 |
|---|---|
| 正確的 git diff | 接受 |
| 前面多一個空行 | `INVALID_ENVELOPE` |
| 用 ```` ```diff ```` 包起來 | `INVALID_ENVELOPE` |
| 少了 `diff --git` 標頭，只有 `---/+++` | `INVALID_ENVELOPE` |
| 空白的 context 行輸出成空字串（少了前導空格） | `INVALID_DIFF` |
| hunk 標頭行數差 1 | `INVALID_DIFF` |
| 新增檔案（`--- /dev/null`） | `INVALID_DIFF`（目前根本不支援新增檔案） |

**判斷**：
- #290 的 11 次失敗是 4 次 ENVELOPE、4 次 DIFF，加上其他。這和上表的「格式偏差」完全吻合。
- 本報告依隱私規則**沒有讀**實際存下的模型輸出，所以這是**高可信度假說，不是已證實的根因**。

**建議**：不放寬任何路徑或範圍檢查。
1. **正規化外層**：去掉前後空白，以及**一層**外層 code fence，再交給同一個嚴格驗證器。
2. **重算 hunk 行數**：概念同 `git apply --recount`。由 host 依實際的 `+`/`-`/context 行重算標頭，再用 `git apply --check` 在 D6 source 上驗證。
3. **改 prompt**：範例改成有 context 行的 hunk。並明說「不支援新增檔案」，或者補上支援。
4. **先離線驗證**：用上表的 7 種樣式寫成測試，再按 #290 的驗收方式跑 N 個公開、低風險的任務。

### P2-3 監控顯示的派工開關可能和真正的閘門不一致（SEV-3，監控）

- llmtool 的閘門（[`worker_dispatch._parse_policy`](../tools/llmtool/llmtool/worker_dispatch.py)）是**全有或全無**：任一 worker 的設定不合規，例如 `decided_by` 不是 `Owner`、evidence 不是 #289 的連結、多了一個 worker、出現重複 key，就會拒絕**全部** worker。
- 監控（[`llmtool_monitor.worker_dispatch`](../scripts/llmtool_monitor.py) 約 2651 行）是逐項判斷，而且 `DECIDERS` 允許 `"PM"`。
- 重現：用暫存的派工檔測試，閘門與監控對 `codex-developer` 的判斷如下：
  ```
  agy decided_by=PM          gate=False WORKER_POLICY_INVALID   monitor=ALLOWED
  gw evidence not #289 URL   gate=False WORKER_POLICY_INVALID   monitor=ALLOWED
  extra worker key           gate=False WORKER_POLICY_INVALID   monitor=ALLOWED
  duplicate codex key        gate=False WORKER_POLICY_INVALID   monitor=ALLOWED
  ```
- 影響：Owner 或 PM 改錯派工檔時，監控說「可以派」，實際上所有派工都會被拒。PM 照規則看監控，就會被誤導。
- 建議：監控直接重用 llmtool 的 `_parse_policy`，或照它做同一套全有或全無的判斷，並從 `DECIDERS` 拿掉 `"PM"`。這屬於 Claude 擁有的監控，可以和 #312 一起排。

### P3-1 監控的 GitHub 動態漏掉大部分留言

- [`GitHubSource.refresh`](../scripts/llmtool_monitor.py)（約 1100 行）只抓一頁 `per_page=50`，畫面卻寫「最近 12 小時」。
- 實測：最近 12 小時有 **124** 則 issue／PR 留言，監控只會顯示最新 50 則。
- 建議：最簡單的做法是在狀態列寫明「只顯示最近 50 則」；或者改用 `--paginate`，加上筆數上限。

### P3-2 `install-skills.sh` 在 worktree 裡執行，會把 skills 連到暫時目錄

- `REPO` 取的是腳本所在的位置。在 D6 或 `.claude/worktrees/*` 裡執行時，`~/.claude/skills/*` 會被 `ln -sfn` 改成指向那個 worktree。worktree 移除後，連結就斷了。
- 建議：用 `git rev-parse --git-common-dir` 找出 canonical root；或者偵測到 worktree 就拒絕執行。
- 順帶一提：README 的 skills 清單少了 `artifact-verification`。

### P3-3 小項目

- `subprocess.run` 沒設 timeout 的有 8 處，都是本機 git 或 make，例如 [`d6_worktree_lifecycle.py`](../tools/llmtool/scripts/d6_worktree_lifecycle.py) 的 74、219、344、369 行，以及 `task_submission_cli.py` 的 643、735 行。風險低，但遇到 git lock 或 FS 卡住會無限等。
- 未使用的 import 有 9 個，例如 `review_exchange.sys`、`configure.Path`、`review_handoff` 的 3 個。另外 `d6_worktree_env._validate_bootstrap_sources` 的 `base_sha`、`head_sha` 參數沒有用到。
- 少了 PyYAML 時，測試套件不會一開始就清楚報錯，而是出現 851 個看不懂的失敗（§2.1）。建議 `make test` 在開頭先檢查依賴是否已安裝。

### 已查證、**不成立**的假說（不需處理）

- 「D6 venv 裡的 editable install 會留一份派工檔副本，Owner 改開關後會出現 `WORKER_POLICY_AMBIGUOUS`」：查了 88 個 D6 venv，都沒有副本。不成立。
- YAML 載入：`config.py` 和 `skills.py` 都是 `SafeLoader` 的子類別，安全。
- `d6_worktree_lifecycle` 的 `exec(compile(...))`：只執行經 hash 驗證過的 blob，符合設計。

## 4. 優化建議

1. **文件 token 成本（P2）**：
   - `tools/llmtool/README.md` 200 KB、`WORKFLOW.md` 145 KB、`ROADMAP.md` 135 KB，合計大約 12 萬 token。每個 agent 依 AGENTS.md 讀 WORKFLOW 或 Roadmap 時都要付這筆成本。
   - 建議各拆成「現行（< 15 KB）」和「歷史歸檔」，做法同 `docs/history/`。這是最直接省 token 的地方。
2. **CI 成本（P3，可以併入 #99／#293）**：
   - 近 14 天碰到 `tools/llmtool` 的 131 個 commit 中，有 **57 個只改 `.md`**，但每個都跑了 2 組約 8 分鐘的 matrix。
   - 可以在 `paths` 加 `!tools/llmtool/*.md`，只排除最上層的文件。注意 `llmtool/skills/*/SKILL.md` 是打包的產品檔，不能排除。
   - 這個私人 repo 沒有 branch protection（API 回 403，免費方案），所以技術上不會卡住 PR。但 PM 的 exact-head CI 合併關卡要先改成「只改文件的 PR 改走文件驗證」，否則 PM 會一直等一個永遠不會出現的 run。
3. **共用安全 I/O helper（P3）**：`object_pairs_hook` 拒絕重複 key 的寫法出現 27 次，atomic write（tmp → fsync → replace → fsync dir）有 7 份實作。P1-1 的鎖問題正是「其中一份沒跟上」造成的。建議抽成一個小模組，例如 `llmtool/safe_io.py`，由各處逐步改用。
4. **監控拆檔（P3）**：同 #313 §5。

## 5. 建議的下一步

| 優先 | 工作 | 誰 |
|---|---|---|
| 1 | 修 P1-1（flock＋有上限的等待＋補測試），讓 main 回到綠燈 | PM 派 Developer，Claude 做正式審查 |
| 2 | P2-2 的 gw 輸出正規化與重算行數，含 7 種樣式的離線測試 | PM 派 Developer（併入 #290） |
| 3 | P2-3、P3-1 的監控修正 | Claude（監控歸 Claude 管），可以併入 #312 之後 |
| 4 | 文件拆分與 CI paths 調整 | PM 排程 |

## 6. 本報告沒有做的事

- 沒有修改任何程式碼、派工檔、D6、Roadmap 或 skill。
- 沒有讀取任何真實 TaskStore 內容或模型輸出；所有重現都用暫存目錄和合成資料。
- 沒有呼叫任何 provider 或模型。
- nlf 依 Owner 指示不在範圍內。
