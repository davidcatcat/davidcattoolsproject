# PR #84 @ `64aa0f4`：整合後的 Mac 實測證據與 C2 EPIPE 可行設計（2026-10-10）

> 撰寫：Claude Code（Opus 5.5），在 Mac canonical repo 上
> 本批工作的 Mac 執行者：Claude Code。只執行測試，沒有修改任何原始碼或測試檔。開始前確認過：工作樹乾淨、沒有其他寫入者或測試正在執行，並以 fast-forward 對齊遠端 HEAD。
> 獨立性：這份是**執行證據**，不是 Security/QA gate。Claude 是原始 M3 測試工具的作者，因此不能擔任 C2/C3 的獨立 reviewer。

## 1. Gate (1)：整合後 exact-head 證據

| 檢查 | 結果 |
| --- | --- |
| PR #84 HEAD | `64aa0f4c09356d0c0adf085dbcceb7434e506896`（`fca30d8` + 併入 #87，只改 workflow） |
| CI run 38036176798（PR）與 38036173975（push） | Python 3.9 與 3.14 **全部 PASS**；每個 job **Verifier checks: 12/12 PASS; no skips**、**Ran 1372 tests** |
| Mac 完整測試 `.venv`（Python 3.13.3） | **1372 tests OK**（skipped=3） |
| Mac 完整測試 `/usr/bin/python3`（3.9.6） | **1372 tests OK**（skipped=4） |
| Mac 原生驗收 `run_containment_acceptance.py` | **14/14 PASS**；`parent_sigkill_plain` 與 `parent_sigkill_popen` 各 20 次，全部 containment confirmed，最大延遲 **0.014 s** |
| 殘留的 host/supervisor/probe 程序 | 無 |
| 殘留的 `llmtool-f1-*` fixture 目錄 | 0 |
| 執行後的工作樹 | 乾淨 |

原生證據檔（Mac 私有）：`.ai-loop/20261009T164035Z-87238/mac-native-claude-64aa0f4.json`，SHA-256 前 12 碼為 `fe1d1668f4a4`。

## 2. C2 EPIPE：可行的純測試設計（只是提案，尚未實作）

**問題：** 現有的 `test_launcher_loss_after_ready_cancels_and_host_retains` 刻意保留一個重複的 Host status reader，所以 Host 回報時不會遇到 EPIPE，無法證明 C2 的要求。

**可行性（已在這台 Mac 上確認）：** 寫入端 pipe 的 peer handle 可以從外部查到。`lsof -p <pid> -d <fd>` 在讀取端開啟時顯示 `PIPE 0x… ->0x…`，最後一個讀取端關閉後，`->0x…` 會消失。此時寫入會得到 `BrokenPipeError`（errno 32）。libproc 的 `PROC_PIDFDPIPEINFO` 也能取得同樣的 `pipe_peerhandle`，不必依賴 `lsof`。

**建議流程**（只用 fixture，所有訊號都沿用 M4/M5 的身分綁定機制）：

1. 照現有方式凍結真正的 `git add` 子程序，確保 READY 已完成。
2. 用綁定身分的 SIGSTOP **凍結 Host**。
3. 殺掉 launcher（driver），並關閉測試端持有的重複 reader。
4. **證明最後一個讀取端已經關閉：** 查 Host fd 1（status pipe 寫入端）的 peer handle，必須是空的，而且列舉 fixture 中所有已登記程序的 FD，都不能再持有這個 pipe 的讀取端。
5. 用綁定身分的 SIGCONT 恢復 Host。Host 偵測到 parent 遺失，取消 supervisor，進入 `_unknown`，接著執行必定發生的 `_emit`。由於步驟 4 已確認沒有任何讀取端，這次寫入在 kernel 層面必然是 EPIPE。
6. 驗證以下各項：
   - Host 保持存活並持續 operator-wait，觀察時間至少為 run budget 加上安全餘裕；
   - 沒有任何 RESULT；
   - supervisor 與 git 已被 last act 清除，Host 沒有自行 reap、沒有回滾，也沒有重試；
   - 最後由獨立的 harness cleanup 證明原始那一代程序都已不在。

**限制：** 不使用 root 或 dtrace，就無法直接觀察 Host 內部捕捉到的 `BrokenPipeError`。證據鏈改為三段：kernel 證明沒有讀取端、Host 程式碼中 `_unknown` 一定會先 emit、可觀察到的保留行為。若 reviewer 要求直接觀察到例外本身，只能改成需要額外權限的做法，那屬於 P0（需要 root），必須另外決定。

**未指派寫入者：** 依 P-5，請 coordinator 指定唯一一位 Mac 寫入者實作這個設計，並由非作者的 reviewer 驗證。

## 3. C3

C3 仍是 NOT_VERIFIED。依 coordinator 指示，需要由獨立 Security reviewer 在最終 SHA 上逐條裁定，Claude 不擔任這個角色。

你需要做：無
