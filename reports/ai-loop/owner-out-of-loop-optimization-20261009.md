# AI 團隊協作優化：把需求方移出傳話迴圈（2026-10-09）

> 撰寫：Claude Code（Opus 5.5），在 Mac canonical repo 上，應需求方要求整理
> 讀者：AI Coordinator（ChatGPT）
> 類型：流程建議報告。這不是 review 或 QA gate，也不改程式、控制腳本或規則檔
> 依據：需求方 2026-10-09 在 Claude 對話中的原話，以及當天 Issue #56、#70、#74 與 PR #57–#76 的實際紀錄

## 1. 需求方的原話（問題來源）

> 「我不要又再給我看技術性判斷的approve, 你們AI自行協調好就好, 原則上以需求方我一定會approve啊, 我又不知道有什麼情況我不要approve」

> 「明明就是你們ai自己寫出來的東西,難道還要人來維護」

> 「我知道他停下來了, 但是不會把報錯, 先去修好, 或是丟回給git讓你審查, 再丟回給codex去修好, 再繼續嗎?」

> 「停下來要人審查什麼我也不懂, 我也不知道風險, 對吧? 我還不是一樣貼給你們去修理」

**結論：** 需求方在這些停止點沒有提供任何判斷，只是在傳話和按按鈕。
停止點本來就是給 AI 審查用的；需求方因為 AI 之間沒有接通而被卡在中間。
目標不是讓需求方看懂技術內容，而是**把需求方從迴圈中拿掉**。
需求方只保留三件事：目標確認、成果驗收，以及真正的商業或權限決定。

## 2. 今天需求方被迫擔任的傳話或按鈕角色

| 類型 | 實例 | 需求方實際加了什麼判斷 |
| --- | --- | --- |
| 在 Mac 執行 AI 寫的指令 | `--qa-low`、`--qa-low-review-diagnose`、F1 Codex 啟動 | 無，照貼照跑 |
| 把 AI A 的話轉給 AI B | 把 ChatGPT 的進度或要求貼給 Claude，把 Codex 的 STOP 輸出貼給 Claude | 無 |
| 批准技術版本 | 鎖版 launcher 的 `--approve`（已撤回） | 無；需求方表示一定會批准 |
| 本機手動操作 | 恢復 `run.sh` 捷徑、關閉 PR、貼 Issue 留言（Claude 的權限檢查擋下時改由需求方執行） | 無 |
| 確認技術處理方式 | 6 條 Low 的 ACCEPT/DEFER | 只有目標層級的「同意」 |

## 3. 根本原因：三個斷點

1. **GitHub 到 Claude 之間沒有自動通道。** Coordinator 在 PR 中提出 Claude 審查要求後，必須等需求方開 Claude Code 對話並說「review repo」。
   ai-loop 腳本內的 `claude -p` 是一次性審查員，只審 Codex 的差異，不會處理 PR 上的審查要求。
2. **GitHub 到 Mac 之間沒有自動觸發。** Coordinator 無法存取 Mac。任務準備好之後，必須由需求方手動執行 `run.sh`。
3. **STOP 訊息的寫法像是要給人處理。** 例如 `STOP: Codex reported NEEDS_DECISION: C1 frame-size blocker...` 是技術語言，看起來需要人來解讀，但實際上下一步完全是 AI 的事：Coordinator 修改契約，Claude 審查，再重新啟動。

## 4. 建議修改（依優先順序）

### P1 Claude 審查通道：自動處理，不需要需求方轉告

- **約定：** 需要 Claude 審查時，Coordinator 在 PR 或 Issue 留言，用固定開頭標記，例如 `CLAUDE-REVIEW-REQUEST: <PR/Issue>`，並寫明 exact SHA 與問題。這個格式今天的 PR #68、#72、#73、#75、#76 其實已經大致在用。
- **Claude 端：** 用 Claude Code 的排程功能，定時掃描 open 的 PR 和 Issue，找出還沒處理的審查要求，然後審查並回覆留言。
  - 只需要 GitHub 和原始碼的審查，可以由雲端排程直接完成。
  - 需要 Mac 本機執行的部分（例如 Mac 原生測試、本機收尾）留到下次在 Mac 上的 Claude 會話，並在 Issue 上標記「Mac pending」。
- **效果：** 需求方不再需要說「review repo」。
- **狀態：** Claude 端可以立即設定，等需求方一句「好」即可啟用。

### P2 Issue #74 的自動安全檢查（P3 的前提）

Issue #74 已經在處理，這裡列出建議的最小範圍：

- `scripts/ai-loop.sh` 在 `exec` 之前，確認 exact fetched SHA 的 `ai-loop helper CI` 為 `success`；失敗、未知或查不到都 STOP。
- 在 ai-loop CI 矩陣加入 `macos-latest`。PR #62 的 `/var` symlink 失敗屬於這一類，可以在 CI 先抓到，不必等到需求方的 Mac 上才發現。
- 任務啟動的 commit 只改 `automation/ai-loop/task.*`，並且確認 `git diff <prev>..<new> -- scripts/` 為空。這和 PR #75 的 R2 相同。

### P3 Mac 自動觸發：不需要需求方按 `run.sh`

前提是 P2 已完成。

- 在 Mac 上用 launchd 定時（例如每 5 分鐘）跑一個極小的檢查：控制分支 tip 是新的 READY 任務、該 SHA 的 CI 為 success、`scripts/` 的變更已有 Claude 審查紀錄，而且目前沒有 ai-loop 在執行（沿用現有 lock）。條件都成立才執行 `run.sh`。
- 結果照舊自動發布到 GitHub（現有的 diagnostics 發布功能）。Coordinator 讀取結果後繼續下一步。
- **風險（白話）：** AI 推上 GitHub 的程式會在無人看管的情況下於 Mac 執行。
  P2 的強制檢查（CI 通過、`scripts/` 有 AI 審查）會把這個風險降到不高於現況：需求方本來就看不懂內容，照樣會按下去。
- 需求方只需要安裝一次 launchd 設定。這屬於權限邊界的變更，安裝前要讓需求方知道上面這段白話風險。
- 這是 A11c 原生 llmtool 上線前的過渡做法，不擴充成第二套永久系統。

### P4 STOP 訊息改成告訴需求方「你不用處理」

ai-loop 的終端輸出最後一行，依照 STOP 類型改寫成給需求方看的白話：

- **契約或規格問題**（例如 NEEDS_DECISION）、審查 FAIL、CI 失敗：
  「已停止並保留現場；問題已回報 GitHub，AI 團隊會處理。你不需要做任何事。」
- **只有真的需要人時**（例如 Mac 權限、帳號登入、商業決定）才寫：
  「需要你：<一句話>」。

技術細節照舊留在 log 和 GitHub diagnostics，不放在給需求方看的最後一行。

### P5 Coordinator 給需求方的訊息格式

每則交接訊息的最後固定一行：

- 「你需要做：無」，或者
- 「你需要做：<一個可複製的指令或一個是非題>」

不要再出現需要需求方理解 SHA、CI、gate 的句子。今天的交接訊息已經有改善（例如「你不需要逐項核准技術版本」），建議固定成格式。

### P6 規則檔重複（延續 PR #69 L1、PR #72 F3）

同一套協作規則目前寫在 `CLAUDE.md`、`AGENTS.md`、`ai-team-delivery`、`safe-git`、`WORKFLOW.md`、`ROADMAP.md`、`README.md`、`NATIVE_SELF_DEV_MVP.md`。
PR #72 為了改一條規則要動 8 個檔案。
建議以 `skills/ai-team-delivery/SKILL.md` 為唯一來源，其他檔案只保留一行連結。
P1–P5 落地後只需要改那一個檔案。

## 5. 今天的經驗（供 Coordinator 參考）

- **做對的：**
  - Codex 在 F1 遇到規格矛盾（16 KiB vs 57 KiB）時正確停下，沒有自行放寬限制。
  - PR #76 的 R1（Unicode 編碼）與 R2（不做恢復控制器）都照建議快速落地。
  - PR #72 合併後，F1 的修正迅速轉成 Issue #74。
- **要避免的：**
  - 為了一次性的決定建造控制器機制，例如 QA Low 的 PR #57–#62。
  - 把 AI 寫的工具放在 Git 以外（鎖版 launcher，已撤回）。
  - 把 AI 之間的審查要求做成需求方的批准步驟。
- **規格審查的盲點：**
  - 16 KiB 上限在 PR #75 審查時已被列為 L2 風險，但直到 Codex 實際執行才被證實。
  - 編碼放大問題（CJK 的 ASCII 跳脫是 2 倍、emoji 是 3 倍）是在 PR #76 用實際計算才發現的。
  - 建議契約中的數值上限都附一個可以執行的邊界驗證，而不只是文字描述。

## 6. Mac 本機收尾已完成（Issue #70 交接）

在 2026-10-09 於 Mac 上執行，未使用 reset、stash、clean 或 `--resume`：

1. **核對：** 根目錄為 canonical repo；分支 `feat/a11b222-f1-private-git-add-20261009`；HEAD `1f1fe91c4ecd48762c48fdd1e3b5f8c347673c5c`；tracked 檔案與 index 乾淨；唯一的 untracked 檔案是那份報告，4,858 bytes，SHA-256 `7952921d04619a73fe684a4278398b0ae296f682bc15ca4eaa2afcdc0134b0dd`；目標檔名沒有衝突；run 目錄已被 Git 忽略。
2. **搬移：** 用 `mv -n` 移到 `.ai-loop/20261009T152208Z-83675/llmtool_a11b222_f1_mac_acceptance_20261009.md`。移動後仍是 4,858 bytes，SHA-256 相同。run 目錄的原始紀錄都保留。
3. **切換：** `git switch main`，並 fast-forward 到 `24a6eb56b238db92698d8204eb17b28a13b54dc5`，與 `origin/main` 一致，工作目錄乾淨。舊分支 `feat/a11b222-f1-private-git-add-20261009` 保留。

Coordinator 可以照 Issue #70 的計畫準備 F1-r2：只改 task 的 commit、`scripts/` 的 diff 為空、exact SHA 的 CI 通過。

## 7. 請 Coordinator 處理

1. 把 P2 的範圍併入 Issue #74，並為 P3 和 P4 開一個 issue（或併入 #74）。
2. 在 `ai-team-delivery` 採用 P1 的審查要求標記與 P5 的訊息格式，並依 P6 精簡重複的規則。
3. 照 Issue #70 準備 F1-r2。
   - 在 P3 完成之前，啟動仍需要需求方執行一次 `run.sh`。訊息中只給一行指令，並註明「你需要做：執行這一行；其他都不用管」。
