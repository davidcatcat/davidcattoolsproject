# PM 平行派工調查報告

- 日期：2026-10-24（Asia/Taipei）
- 調查者：Claude，在 Owner 直接開的 Claude TUI session 裡進行。只讀資料，沒有派工，也沒有改任何 PM 規則或 Worker 狀態。
- 範圍：Codex PM 主對話 `01a169de-a45e-7260-95f0-bffb7c73d10a` 在 2026-10-24 01:46–13:15 之間的行為；它管理的兩個互動 Claude Developer（#301 R4 `8cbb958d…`、#304 UI `2fa3ef77…`）；PR #308 審查派工被暫停的經過。
- 起因：Owner 觀察到「PM 沒有平行、自動派工」。直接觸發點是 13:08 Owner 要求對 #308 `c70c561` 派審查，PM 卻回覆「先暫停該次審查派工」。

## 1. 結論

PM 沒有持續平行派工，主因不在個別判斷錯誤，而是下面三件事疊在一起：

1. **PM 只在 Owner 傳訊息時才會動（結構性，最主要）。** Codex PM 是對話式 agent。每一輪工作（turn）都是 Owner 的訊息觸發的；一輪結束後，沒有任何機制會再叫醒它。今天兩輪之間最長空檔 5 小時 11 分。這段時間 PM 完全沒有動作，所有等它回應的工作都停住。同一輪之內 PM 確實會平行派出多個 subagent。
2. **互動 Claude Developer 用手動權限模式，每一步都要 PM 回應。** 兩個 Developer 都用 `--permission-mode manual` 啟動，由 PM 透過 PTY 回應權限確認。PM 沒在跑，就沒人回應。R4 今天在權限確認卡了 **5 小時 19 分**和 **49 分鐘**，兩次都在 Owner 傳訊息叫醒 PM 後 1 分鐘內解除。
3. **同一輪內收到互相衝突的指示，PM 選擇了最新、最保守的那個。** 13:08:06 Owner 要求「對 c70c561 派審查」。58 秒後 13:09:04 又收到一段長提示，裡面對 #308 寫的是「先確認實際 Owner、範圍、Writer 和已授權修復路線……不得由 PM 擅自接管修改」。PM 照後者先暫停。這次暫停是照指示做的，不是 PM 自作主張；問題在兩個指示來源對 #308 的說法不一致。

另外有一個**需求本身的矛盾**，要 Owner 決定：提示要求 PM「不要每完成一個工作包就停下等 Owner」，同一份提示又禁止「新的 Supervisor、Daemon 或繞過 Gate 的方式」。但在現行架構下，讓 PM 在兩輪之間繼續做事，正需要某種排程或喚醒機制（見第 4 節）。

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

## 4. 建議

依影響大小排列。第 1 項牽涉架構和「不得新增 Supervisor／Daemon」的限制，要 Owner 決定；其他是 PM 規則的文字修補，可由 PM 照一般流程處理。

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

- 沒有改 `pm-continuation.md`、`AGENTS.md`、`worker-dispatch.json` 或任何提示。
- 沒有對任何 session 送指令，也沒有回應 R4 的權限確認。
- PM 對話內容只讀了 Owner 訊息的時間和內容摘要，用來對時間軸；沒有讀模型輸出和工具輸出。Codex 資料庫只讀了輪次的狀態和時間，以及 subagent 的名稱和路徑。
- 數字來自 Owner 這台 Mac 的本機紀錄，時間都是 Asia/Taipei，查詢時間到 13:15 為止。
