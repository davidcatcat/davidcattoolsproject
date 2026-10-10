# PR #84 C2 closed-last-reader EPIPE：純測試實作與 Mac 證據（2026-10-10）

> 撰寫者 / Mac 唯一寫入者：Claude Code（Opus 5.5）。已在 PR #84 公開認領（[留言](https://github.com/davidcatcat/davidcattoolsproject/pull/84#issuecomment-6095634002)），並在 preflight 確認：HEAD 為 `64aa0f4` 且與 remote 一致、工作樹乾淨、沒有其他執行中的寫入者。
> 性質：**開發者證據**。C2 測試由 Claude 撰寫，因此需要**非作者**的 reviewer 驗證；本報告不是 Security/QA gate。
> 範圍：只修改 `tools/llmtool/tests/test_git_add_product_failures.py`（+75 行）。沒有改動正式程式、workflow 或 ai-loop。

## 1. 版本

| 項目 | 值 |
| --- | --- |
| 修改前 | `64aa0f4c09356d0c0adf085dbcceb7434e506896` |
| 修改後（PR #84 新 HEAD） | `5166a417a1bf7a448ee1c167e4ced669acaa5b20` |
| 測試檔 SHA-256 前 16 碼 | `ce1402daf79ac363` |

## 2. 設計：如何證明這是真正的 EPIPE

**問題：** 原本的 launcher-loss 測試會保留一個重複的 Host status reader，所以 Host 回報時不會遇到 EPIPE。

**做法：** 新增 `test_last_status_reader_closed_after_ready_epipe_retains_unknown`，步驟如下：

1. `run_frozen()` 凍結真正的 `git add`，以此證明 READY 與 supervisor 的最終檢查都已經完成。
2. 用 libproc `PROC_PIDFDPIPEINFO` 讀取 Host fd 1（status 寫入端）的 `(handle, peer)`，以及測試端重複 reader 的 `(handle, peer)`。兩組必須互為對方，證明這個 reader 確實就是 Host status pipe 的讀取端。
3. 關閉測試端的重複 reader。此時 Host fd 1 的 peer 仍然存在，因為 launcher 還在讀，Host 也還沒有回報任何東西。
4. 用身分綁定的方式對 launcher 送 SIGKILL。launcher 是最後一個讀取端。kernel 會在 launcher 的退出狀態可被觀察到之前，就先關閉它的所有描述符；而 Host 只有在觀察到 launcher 消失之後才會進入 `_unknown` 回報。
5. 斷言 Host fd 1 的 peer 為 **0**，也就是任何程序都不再持有讀取端。因此 Host 在 `_unknown` 中必然執行的 `_emit` 是一次真正的 EPIPE 寫入。這台 Mac 上已驗證：讀取端關閉後 peer 變成 `0x0`，寫入會得到 `BrokenPipeError`（errno 32）；libproc 讀到的偏移與 `lsof` 結果一致。
6. 斷言取消流程確實執行：supervisor 與 git 都已結束，也就是 supervisor 的 last act 生效。
7. **結構性保留證明** `prove_structural_retention`：在 `timeout + 2 + 5 + 1` 秒內（大於所有產品期限：run budget、2 秒 handshake、5 秒 cleanup，再加安全餘裕），以下條件必須一直成立：
   - Host 存活；
   - 已結束的 supervisor 仍是 Host 底下**未被回收**的 zombie，PID、UID 與啟動時間都和綁定時一致；
   - Host fd 1 仍是同一個 handle，peer 仍為 0。

   正常完成的 Host 會在期限內回收 supervisor 並退出；只有 `_unknown` 會一直保留它。因此這是依產品行為推得的證據，而**不是**「時間到了就算保留」。這也符合 Codex 的 `test_elapsed_observation_never_grants_unknown_host_signal_authority` 規則。只有在這個證明成立之後，才授予 teardown 權限（`host.retained`）。
8. 斷言沒有 RESULT、也沒有 FRAME（沒有任何讀取端能收到）。

**負向測試：** `test_structural_retention_rejects_reaped_or_released_anchor` 驗證：在 incident 發生前（supervisor 還活著），證明會失敗，而且**不會**授予 `retained`。

**限制：** 在沒有 root 或 dtrace 的情況下，無法直接觀察 Host 內部被吞掉的 `BrokenPipeError`。因此證據鏈由三部分組成：kernel 證明沒有任何讀取端、`_unknown` 的程式路徑一定會先 emit、以及可觀察到的保留行為。若 reviewer 要求直接觀察到這個例外，就需要額外權限，屬於 P0（root）範圍，必須另外決定。

## 3. Mac 實測（最終版本；這是 EPIPE 解決後的唯一一次完整執行）

環境：macOS 15.7.9。

| 檢查 | 結果 |
| --- | --- |
| 新測試 ×5（`.venv`）、×1（`/usr/bin/python3`） | 全部 OK；沒有殘留程序；0 個 fixture |
| 完整測試 `.venv`（3.13.3） | **1374 tests OK**（skipped=3） |
| 完整測試 `/usr/bin/python3`（3.9.6） | **1374 tests OK**（skipped=4） |
| 原生驗收 | **14/14 PASS**；SIGKILL plain 與 Popen 各 20 次，全部 contained，最大延遲 **0.012 s** |
| 殘留的 host/supervisor/probe 程序與 fixture 目錄 | 無 / 0 |

原生證據檔（只留在 Mac）：`.ai-loop/20261009T164035Z-87238/mac-native-claude-c2.json`，SHA-256 前 12 碼 `9ffd007f84c7`。

## 4. 待辦事項（由 AI 處理，依 P0 不需要需求方）

1. 在 `5166a41` 上的 exact-head CI（3.9 / 3.14）。
2. **非作者 reviewer** 驗證 C2，不能是 Claude；Codex 不是這次 C2 的作者，可以擔任。
3. C3 逐條裁定（獨立 Security reviewer）。
4. 最終獨立 Security 與 Functional QA。由於這次測試只改了測試檔，若 C3 不需要再改程式，本報告第 3 節的 Mac 結果可以作為最終版本的 Mac 證據。

C2 寫入者認領已釋放。

你需要做：無
