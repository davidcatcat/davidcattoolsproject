# PM 平行派工調查報告

- 日期：2026-10-24（Asia/Taipei）
- 調查者：Claude，在 Owner 直接開的 Claude TUI session 裡進行。只讀資料，沒有派工，也沒有改任何 PM 規則或 Worker 狀態。
- 範圍：Codex PM 主對話 `01a169de-a45e-7260-95f0-bffb7c73d10a` 在 2026-10-24 01:46–13:15 之間的行為；它管理的兩個互動 Claude Developer（#301 R4 `8cbb958d…`、#304 UI `2fa3ef77…`）；PR #308 審查派工被暫停的經過。
- 起因：Owner 觀察到「PM 沒有平行、自動派工」。直接觸發點是 13:08 Owner 要求對 #308 `c70c561` 派審查，PM 卻回覆「先暫停該次審查派工」。

## 1. 結論

PM 沒有持續平行派工，主因不在個別判斷錯誤，而是下面三件事疊在一起：

1. **PM 只在 Owner 傳訊息時才會動（結構性，最主要）。** Codex PM 是對話式 agent。每一輪工作（turn）都是 Owner 的訊息觸發的；一輪結束後，沒有任何機制會再叫醒它。今天兩輪之間最長空檔 5 小時 11 分。這段時間 PM 完全沒有動作，所有等它回應的工作都停住。同一輪之內 PM 確實會平行派出多個 subagent。
2. **互動 Claude Developer 用手動權限模式，每一步都要 PM 回應。** 兩個 Developer 都用 `--permission-mode manual` 啟動，由 PM 透過 PTY 回應權限確認。PM 沒在跑，就沒人回應。R4 今天在權限確認卡了 **5 小時 19 分**和 **49 分鐘**，兩次都在 Owner 傳訊息叫醒 PM 後 1 分鐘內解除。
3. **PM 把「不得修改」解讀得太寬，又剛好在同一輪收到看起來衝突的指示。** 13:08:06 Owner 要求「對 c70c561 派審查」。58 秒後 13:09:04 又收到一段長提示，裡面對 #308 寫的是「先確認實際 Owner、範圍、Writer 和已授權修復路線……不得由 PM 擅自接管修改」。PM 照後者暫停了審查派工。

   但「不得接管修改」和「Owner 授權派獨立審查」本來不矛盾：前者限制的是誰能改監控程式，後者只是派 Reviewer 看程式。PM 把修改限制延伸成審查限制，屬於指令範圍判讀不精確。提示沒有明確區分這兩件事是誘因，但不能把原因全部歸給提示衝突（初版報告這點寫得不夠，見 §6.4）。

初版報告把下一步寫成 (a)/(b)/(c) 三選一，經 Owner 與 Coordinator 討論後已修正：這三項分別處理不同環節，不能互相替代。採用方向見 §6。

## 2. 證據

### 2.1 PM 主對話每一輪的時間軸（來源：Codex `~/.codex/thread_history_1.sqlite` 的 `thread_turns`，只讀狀態和時間）

| 輪次 | 開始 | 結束 | 長度 | 觸發的 Owner 訊息（摘要） |
|---|---|---|---|---|
| 1 | 01:49:27 | 02:22:08 | 33 分 | A11c 續做 |
| 2 | 02:28:58 | 02:59:59 | 31 分 | 「canonical 是乾淨的，請自己重查」 |
| 3 | 03:31:17 | 06:03:08 | 2 時 32 分 | R4 主線；同一輪內又插入 #301、#304 指派 |
| — | **06:03:08** | **11:14:34** | **5 時 11 分空檔** | 沒有任何輪次 |
| 4 | 11:14:34 | 11:30:37 | 16 分 | 「繼續」 |
| — | 11:30:37 | 12:03:46 | 33 分空檔 | |
| 5 | 12:03:46 | 12:06:33 | 3 分 | 轉貼監控結論 |
| 6 | 12:06:59 | 12:16:14 | 9 分 | 轉貼 ⚠ 權限確認提醒 |
| 7 | 12:17:07 | 12:47:58 | 31 分 | 「沒做完就繼續做啊」＋ 12:33 長提示 |
| — | 12:47:58 | 13:08:06 | 20 分空檔 | |
| 8 | 13:08:06 | 進行中 | | #308 派審查 ＋ 13:09 長提示 ＋ 13:11 Owner 澄清 |

全部 8 輪都是 Owner 傳訊息觸發的，沒有一輪是 PM 自己接著開始的。

### 2.2 同一輪之內確實會平行（來源：Codex `state_5.sqlite` 的 `thread_spawn_edges`，以及每個 subagent 自己的 `thread_turns`）

| subagent | 工作 | 執行時間 |
|---|---|---|
| Carver | issue267 timeout regression | 02:52–04:33 |
| Hypatia | R4 app-server adapter dev | 03:36–06:00 |
| Gauss | issue267 tracking | 04:02–04:03 |
| Kepler | R2 repair intent dev | 11:24–11:28 |
| Parfit | UI session status | 12:15 |

第 3 輪裡 Carver、Hypatia、Gauss 的執行時間有重疊，代表 PM 在一輪之內會平行派工。缺的是「一輪結束後繼續派」。

### 2.3 Claude Developer 等 PM 回應的時間（來源：兩個 session 自己的 `~/.claude/projects/…/<session>.jsonl` 時間戳，以及 `~/.claude/sessions/<pid>.json` 的狀態欄位）

| Developer | 等待開始 | 等待結束 | 時長 | 等的是 | 解除時 PM 的狀態 |
|---|---|---|---|---|---|
| #301 R4 `8cbb958d` | 05:55 | 11:14 | **5 時 19 分** | 工具呼叫後的權限確認 | Owner 11:14:35「繼續」開始第 4 輪 |
| #301 R4 `8cbb958d` | 11:18 | 12:07 | **49 分** | 權限確認（登記檔 `waitingFor: permission prompt`） | Owner 12:06:59 轉貼 ⚠ 提醒，開始第 6 輪 |
| #301 R4 `8cbb958d` | 12:45:41 | 13:15 仍在等 | 30 分以上 | 權限確認 | 第 8 輪進行中，PM 還沒處理到 |
| #304 UI `2fa3ef77` | 04:19:42 | 13:15 仍閒置 | **約 9 小時** | 做完一輪後等下一個指示 | Owner 12:24 指示用 `/exit` 正常結束，到 13:15 程序仍在 |

另外，`tools/llmtool/worker-dispatch.json`（main）裡 `claude-developer` 是 `OWNER_ASSIGNMENT_ONLY`，`claude-reviewer` 和 `codex-developer` 是 `ALLOWED`。#301、#304 都有 Owner 指派，所以派工開關沒有擋住這兩個工作，可以排除是開關造成的。

### 2.4 #308 被暫停的經過（來源：PM 主對話的 Owner 訊息時間戳）

| 時間 | 來源 | 對 #308 的內容 |
|---|---|---|
| 12:33:57 | 長提示（Coordinator 格式，Owner 轉貼） | 「Owner 自己透過 Claude TUI 開發的監控工具是獨立項目，不要納入本輪 Claude Developer 或 UI Agent 的工作與驗收」 |
| 13:08:06 | Owner | 「叫 PM 對 c70c561 派新一輪審查……PASS 之後合併」 |
| 13:09:04 | 長提示（Coordinator 格式，Owner 轉貼） | 「P1 — PR #308：先確認這個 PR 的實際 Owner、範圍、Writer 和已授權修復路線。若屬 Owner 獨立監控工具……不得由 PM 擅自接管修改」 |
| 13:11:08 | Owner | 「#308 是我直接管的監控工作，Claude 修復已授權；請對 c70c561 派審查，同時照常處理 #301、#304」 |

13:09 的提示要求先確認邊界，PM 就照做暫停。兩份長提示裡的「不得接管修改」，本意是不要讓 PM 或 Developer 改監控程式；但「派審查」不是接管修改。提示沒把這兩件事分開，PM 就把審查一起停了。

## 3. 和現行規則的對照

`skills/ai-team-delivery/pm-continuation.md` 已經要求平行、持續推進：

- 「A Reviewer FAIL is a repair step for that Task. It does not stop the whole flow.」
- 「One unavailable Worker or one BLOCKED Task does not block other independent, authorized Tasks. The PM continues dispatching them.」
- 「While the escalation is open, only the dependent action waits.」

規則的方向是對的，缺的是：

1. **沒有「什麼時候繼續」的觸發。** 規則規定每個結果之後要做什麼決定，但前提是 PM 正在跑。一輪結束後，沒有東西會讓 PM 回來看 Developer 是否在等它。
2. **沒寫「新的 Owner 訊息只會增加或調整優先順序，不會暫停其他已授權工作」。** 同一輪內插入新訊息時，PM 只能自己判斷新舊指示怎麼合併。
3. **沒區分「接管修改」和「派審查」。** Owner 直接管的項目（`CLAUDE.md` 寫明監控工具由 Claude 負責）不讓 PM 改，但派一個獨立 Reviewer 審查不是修改，應該可以照常派。
4. **互動 Developer 的權限確認沒有回應期限，也沒有升級方式。** 等了 5 小時，沒有任何人收到通知。

## 4. 初版建議（已由 §6 取代）

本節保留初版內容，作為討論紀錄。第 1 項的三選一已由 §6 的 (c) + (d) 取代；第 2–5 項仍然適用，但改由 §6.5 對照 WP3 後再決定放在哪裡。

1. **（Owner 決定）讓 PM 在兩輪之間被叫醒。** 選項：
   - (a) 允許一個只負責「到點送一句『繼續』給 PM」的排程。它不派工、不繞過 Gate，所以應該不算禁止的那種 Supervisor。
   - (b) 維持現狀，接受 PM 只在 Owner 傳訊息時運作，由 Owner 或 Coordinator 定時送「繼續」。
   - (c) Developer 改用非互動、單次執行的呼叫（`claude -p` 加限定工具），讓結果自動回到 PM 那一輪。

   不決定這一項，第 2–4 項只能縮短延遲，沒辦法真正做到自動平行。
2. **（PM 規則）新訊息不暫停其他工作。** 在 `pm-continuation.md` 加一句：Owner 或 Coordinator 的新訊息只會增加工作或調整優先順序；除非明確寫要暫停哪一件，其他已授權工作照常進行；同一輪內指示互相衝突時，只暫停衝突的那一件，並在同一輪內寫明要誰決定。
3. **（PM 規則）分開「不得修改」和「可以審查」。** Owner 直接管的項目，PM 和 Developer 不改程式，但派獨立 Reviewer、讀 CI、照 Owner 指示合併都可以照常做。
4. **（PM 規則）互動 Developer 的權限等待要有期限。** 每輪開始時，PM 先查所有自己開的互動 session（例如用 `claude agents --json` 或監控工具顯示的「等人確認權限，要PM（Codex）回」），優先處理等待中的；等待超過一個期限（建議 15 分鐘）就在對應 Issue 留紀錄，並決定是放行、拒絕還是改派。
5. **（Coordinator 提示）同一份提示不要同時要求「持續自動」和「禁止任何喚醒機制」。** 第 1 項決定之後，提示要跟著改成一致的說法。

## 5. 本報告沒有做的事

- 沒有改 `pm-continuation.md`、`AGENTS.md`、`worker-dispatch.json`、`ROADMAP.md` 或任何提示。
- 沒有啟動 Codex Goals，沒有設任何目標，也沒有派工。
- 沒有對任何 session 送指令，也沒有回應 R4 的權限確認。
- PM 對話內容只讀了 Owner 訊息的時間和內容摘要，用來對時間軸；沒有讀模型輸出和工具輸出。Codex 資料庫只讀了輪次的狀態和時間，以及 subagent 的名稱和路徑。
- §1–§5 的 Session 與時間軸資料來自 Owner 這台 Mac 的本機紀錄，時間都是 Asia/Taipei，查詢時間到 13:15；§6.5 的唯讀版本/help/資料表查證約在 13:20。沒有讀取模型輸出或 Provider 憑證。

## 6. 補充（2026-10-24，Owner 與 Coordinator 討論後）

依 Owner 與 Coordinator 的討論紀錄，採用「(c) + (d)」作為**架構方向**。這項方向決定不等於實作授權、Worker 派工、Goals 實驗授權或正式啟用；本節只記錄方向、待驗證假設與本機查證，不改正式設定、不啟動 Goals、不派工。實作仍須依現行 dispatch、D6、資料、預算、審查與 CI Gate 取得對應 Task 授權。

### 6.1 問題要拆成三個環節，不能三選一

本報告揭露的其實是兩個不同的缺口：

- **PM 缺少自主接續能力**：PM 不是不會平行，而是一輪結束後就不再運作（§2.1、§2.2）。
- **Developer 太依賴 PM 即時操作**：Developer 已經接到工作，卻因為權限確認卡住好幾個小時（§2.3）。

要解決這兩個缺口，得分成三個環節設計：

| 環節 | 負責 | 採用方向 |
|---|---|---|
| 工作執行 | Developer | (c) 非互動執行，用任務級工具白名單 |
| 結果回傳 | Developer → llmtool Core | 結果、完成及互動 Session 的權限等待作為事件，綁定 Task ID、Run ID 和原 Session，持久化並去重；非互動工具拒絕則回傳 `TOOL_DENIED` 結果，不形成等待事件 |
| PM 接續 | llmtool Core → 原 PM | (d) Core 收到可信事件後恢復原 PM，由 PM 自己決定下一步 |

初版的 (a) 定時送「繼續」不採用，因為有空轉、重複觸發和浪費 token 的風險。(b) 由 Owner 或 Coordinator 定時叫醒也不採用，因為等於繼續人工傳話。只做 (c) 也不夠：Developer 交回的結果，還是需要有東西讓已結束的 PM 重新開始。

### 6.2 (c) Developer 非互動執行

- Developer 改用非互動模式，例如 `claude -p --permission-mode dontAsk --allowedTools …`。預期白名單外操作會以拒絕結果結束，但本報告只查到 CLI help 列出相關選項，未驗證拒絕行為；不得把它當成既有保證。
- 白名單**依任務**訂在 Task Contract 裡：寫明可用的工具、可寫的路徑，以及可執行的指令樣式。這些是最小權限規則，不是作業系統層的 containment；D6、程序隔離與資料邊界仍須獨立驗證。
- **不能**全面放行 Shell（例如整個 `Bash`），**不能**用 `bypassPermissions`，也不能用任何跳過權限檢查的參數。
- 被拒絕的操作要記成可辨識的結果（例如 `TOOL_DENIED` 加上工具名稱），透過結果回傳交給 PM。由 PM 決定要補白名單、改做法還是升級給 Owner；Developer 自己不能放寬。
- 要注意：白名單太窄時，Developer 會從「卡住」變成「失敗」。例如 R4 需要啟動 codex app-server 和跑 shell，這類任務要先列出最小指令集再派工。上述拒絕、回傳與程序限制目前都尚未由本報告的查證驗收。

### 6.3 (d) llmtool Core 接收可信事件後恢復原 PM

- Core 只做**確定性**的事件處理：
  - 驗證事件來源和 Task/Run/Session 綁定。
  - 持久化並去重。
  - 找到原 PM 的 thread，嘗試用 Codex app-server 的 thread 恢復與 `turn/start` 介面送出一則結構化事件訊息；本機查證沒有驗證此介面在目標版本可用，須列為實作前相容性 Gate。
- Core **不做 AI 判斷**：要不要審查、修復、改派，全部由被恢復的 PM 依 `pm-continuation.md` 決定。
- 這**不是第二套 Supervisor**，原因有三：
  - 它只在收到事件時動作，不定時輪詢 PM。
  - 它不選工作、不派工。
  - 它不繞過 dispatch、D6、審查或 CI 的任何 Gate。
- **目標投遞語意（尚未實作或驗證）：** durable inbox 採 at-least-once 投遞，不宣稱 exactly-once wake。每個來源事件需有穩定且唯一的 `event_id`，並綁定 Task ID、Run ID、Session ID、事件種類與來源序號；同一 `event_id` 若 payload 不同則拒絕並保留診斷資料。
- Core 先持久化為 `PENDING`，再嘗試喚醒原 PM。`turn/start` 被接受只代表 transport 已接收，不是事件 ACK。只有原 PM 依 `event_id` 持久化消費結果後回傳同 ID receipt，Core 才能標為 `ACKED`。若決策要求派工，PM 必須先持久化綁定該 `event_id` 的 dispatch intent；重播時只能恢復／對帳同一 intent 與 Task ID，不能建立第二次派工。若 intent 的執行結果不明，事件維持未 ACK 並對帳原 Task/Run，不可猜成功或另開新工作。
- 程序重啟時只對 durable inbox 中未 ACK 的事件做有上限的 reconciliation，不做週期性 PM polling。PM 忙碌時維持 `PENDING`，等原 turn 結束後單一序列送達；thread 暫不可用時保留事件並做有上限的重試，耗盡後標 `BLOCKED` 但不得刪除或視為 ACK。這些語意仍須經故障注入驗收。
- 事件內容只帶狀態碼和識別碼，不帶模型輸出。R3/R4 的對應是 Roadmap 內既有方向；本文件仍只提出實作映射，不授予實作權限。

### 6.4 #308：PM 過度擴張「不得修改」的限制

把 §1 第 3 點講清楚：

- 13:09 的提示限制的是「PM 不得擅自接管修改」Owner 的監控工具。
- Owner 13:08 的要求是「派獨立 Reviewer 審查」。
- 這兩件事不衝突。PM 把修改限制當成也包含審查，於是暫停了審查派工。這是 PM 對指令範圍的判讀問題；提示沒區分清楚是誘因，但不是唯一原因。
- 建議在 PM 規則裡寫明：Owner 直接管的項目，PM 和 Developer 不改程式；但派獨立 Reviewer、讀 CI、照 Owner 指示合併，都照常進行。

### 6.5 本機查證（Owner 的 Mac，2026-10-24 13:20 前後，只讀）

| 項目 | 結果 |
|---|---|
| Codex 版本 | `codex --version` 是 `codex-cli 0.163.0`；app-server daemon 有 0.163.0 和 0.164.0 兩版 |
| Goals 功能 | `~/.codex/goals_1.sqlite` 有 `thread_goals` 表，狀態欄允許 `active`、`paused`、`blocked`、`usage_limited`、`budget_limited`、`complete`，還有 `token_budget` 和 `tokens_used` 欄位 |
| Goals 使用情況 | `thread_goals` 是 **0 筆**，PM 主對話從沒設過目標 |
| Claude 非互動權限 | `claude --help` 有 `--permission-mode`（含 `dontAsk`）、`--allowedTools`、`--disallowedTools` |
| 現行 Developer 啟動方式 | #301、#304 兩個 Developer 都用 `--permission-mode manual` 啟動（§2.3） |

### 6.6 Codex Goals：先做隔離實驗，不直接套用到正式 PM

Goals 可能讓 PM 在目標未完成時自己開下一輪，但有三點必須先驗證：

1. **會不會空轉**：外部 Developer 還沒交回結果時，PM 是停在 `blocked`／`paused`，還是一直開新一輪去查狀態、白白花 token。
2. **遇到外部等待會不會正確停止**：等外部事件時，Goal 應該進入 `blocked` 並記錄原因，不是自己判斷成 `complete`，也不是無限重試。
3. **能不能安全接續**：外部事件到了以後（例如透過 §6.3 的 `turn/start`），Goal 能不能從原 thread 接續，而且不重做已完成的步驟、不重複派工。

候選實驗條件（**尚未取得執行授權**）：
- 使用與正式環境分離的 `CODEX_HOME`/profile、隔離 thread、合成任務與 loopback mock；不得改共享設定、讀寫正式 PM state、連正式 D6/GitHub 或使用真實 Provider。
- 提議單次總 token budget 上限為 **10,000 tokens**；這只是供審批的方案值，不是目前授權或已花費額度。啟動前需另有 Owner 核准的 Issue/Task Contract，列出隔離方式、實際 budget 與停止條件。
- 記錄每一輪的開始時間、結束時間、狀態和 token 用量。

實驗結果出來前不得在正式 PM 啟用 Goals；即使候選實驗通過，production enablement 仍需獨立 Owner 決策與相應驗收。Goals 只可能協助「PM 自己接續」；Developer 結果回傳仍需 §6.3 的 Core 事件。

### 6.7 對照 A11c WP3 R1–R5（避免重複規劃、重複實作）

依 `tools/llmtool/ROADMAP.md`（main），WP3「automatic continuation」標為 INCOMPLETE。下表是**候選對照**，不是 Owner 核准的實作切分或授權；(c) Developer 非互動與工具白名單不屬於 R1–R5：

| WP3 缺口 | Roadmap 現況 | 本報告的對應 | 備註 |
|---|---|---|---|
| R1 自動派 Reviewer | #285 已合併，但 adapter 仍是 opt-in，沒有正式環境 E2E；#297 已合併 Reviewer dispatch gate | Core 喚醒可能讓 PM 接著派 Reviewer | 候選依賴；不代表 R1 production E2E 完成 |
| R2 FAIL 自動退回原 Developer | 未完成；PM 決策有持久化，但迴圈不是自動的 | TOOL_DENIED/FAIL 事件與 Core 喚醒可能是候選前提 | #120 既有 repair-intent 工作依其 Task Contract 推進 |
| R3 Reviewer 結果出來後恢復 PM | 未完成；`task complete` 仍需呼叫端觸發 | §6.3 的 Core 喚醒方向與 R3 相關 | 候選映射；尚未核准實作 |
| R4 自動接收 Worker 完成事件 | 未驗證；已合併的 callback 狀態不能證明原生事件訂閱或即時喚醒 | §6.1 的結果／完成事件方向與 R4 相關；互動 Session 權限等待可另作事件來源，非互動拒絕則是 TOOL_DENIED | 候選映射；不宣稱已驗收 |
| R5 Worker 可用狀態保存 | #298 bounded observation storage 已合併；Roadmap 狀態更新另列工作 | 不新增 R5 實作 | storage 已合併不等於 live collection/dispatch integration 驗收 |

本報告**不授權新工作包或改變既有 Task Contract**。以下僅為待 Owner/PM 依現行 Gate 審核的候選映射：
- (c) 非互動 Developer 與工具白名單在 WP3 R1–R5 之外，需獨立確定範圍與授權。
- (d) 事件喚醒可與 R3/R4 既有缺口對照；具體放置、Issue 與實作範圍仍須依既有 Task/Owner 授權確認。
- Goals 實驗是候選方案；本報告未取得實驗執行或 production enablement 授權。
- §4 第 2–4 項 PM 規則文字修改仍需依適用的變更流程，不由本報告預先授權。

尚待實際驗收的項目包括：多任務背景接續、事件持久化與 ACK、程序重啟重播、busy/unavailable thread 恢復，以及重播同一事件不會重複派工。

### 6.8 決議、授權與交付狀態

| 面向 | 狀態 |
|---|---|
| Owner 採納的架構方向 | (c)+(d) 如 §6.1 所述；本報告記錄方向，不等於實作核准。 |
| 實作授權 | 本報告沒有提供；須有明確 Owner-approved Issue/Task Contract 並通過既有 D6、dispatch、budget、data、review 與 CI gates。 |
| 已實作 | 本報告描述的非互動 Developer 啟動改造、Core event inbox/ACK/replay 與 Goals 實驗：無。 |
| Live verified | 上述路線：無。§6.5 僅為 local read-only version/help/database inspection。 |
