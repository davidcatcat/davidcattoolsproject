# llmtool A10 Dynamic Context Retrieval re-review（2026-10-02）

## 範圍與方法

- 審查對象：分支 `feat/a10-dynamic-context-retrieval`，HEAD `9543f36621bd325bc8d3a11ebd3bcf0c2229c203`（PR #12，OPEN、MERGEABLE）。
  - 相對 `origin/main` `3923f7a`：ahead 5、behind 0。
  - 本輪新增的 commit：
    - `ae2ddbe`：修正，改了 `retrieval.py`、`retrieval_files.py`、`tests/test_retrieval.py`。
    - `9543f36`：文件，更新 validation report、README、ROADMAP。
- 對照：上一輪的 [A10 independent review](llmtool_a10_dynamic_context_retrieval_review_20261002.md)（A–H，APPROVE WITH CHANGES），以及 Codex 更新後的 [A10 validation report](llmtool_a10_dynamic_context_retrieval_validation_20261002.md) 的 "Independent review disposition"。
- 方式：
  - **唯讀**，在 canonical repo 進行。canonical repo 當時就 checkout 在這個分支，review 時沒有切換分支，也沒有使用 worktree、clone 或專案副本。
  - 逐行閱讀 `8066049..9543f36` 的程式 diff。
  - 以暫存 HOME 與公司 gateway profile 實測：`http://home-llm.example:8000`，route `gw`，`enable_thinking: false`，共 7 次呼叫。
  - fixture 放在 scratchpad：`app/big.log` 340,025 B、`app/diagram.png`、`app/color.log`（ANSI）、`app/plain.log`、`app/libfoo.so`、`app/cache.bin`。另有一個未設定完成的 profile `broken`。

## 結論：APPROVE（可以 merge）

上一輪的 medium 問題 A、B、C 都已修好，G 也修好了；F 部分接受，D、E、H 的處理方式與理由合理。Codex 的報告內容完整，與實際結果一致。剩下 3 個小問題，建議記為 follow-up，不影響 merge。

## 基本檢查

| 項目 | 結果 | 是否與 Codex 報告一致 |
|------|------|----------------------|
| Python 3.13.3（`tools/llmtool/.venv`） | 605 tests OK（1 skipped） | 一致（605／604／1） |
| Python 3.9.6（`/usr/bin/python3`） | 605 tests OK（1 skipped） | 一致 |
| GitHub Actions `llmtool CI`（`9543f36`） | 兩個 run 都 success | 報告註明 final-head CI 待確認，現在已確認 success |
| PR #12 | OPEN、MERGEABLE | — |
| 跑完測試後 canonical repo 的工作目錄 | 沒有變更（只有原本就有的 benchmark／reasoning 未追蹤報告） | — |

## A–H 逐項確認

| # | Codex 的處理 | 程式碼 | 實測 | 判斷 |
|---|-------------|--------|------|------|
| A planner 不知道大小限制、會選到大檔或二進位檔 | FIXED | `build_manifest.add` 排除 `st_size > 256 KiB` 與 21 種副檔名（只看 metadata 與檔名，不讀內容）；`PLANNER_SYSTEM` 寫明 8 個檔案、單檔 256 KiB、總共 512 KiB；`parse_retrieval_plan` 在讀檔前先檢查選中的總量 | 「看 app/diagram.png 與 app/plain.log」：rc=0，PNG 不在清單裡，只讀了 `plain.log`（`control_escaped=false`） | **確認修好**（見 N1、N2） |
| B ANSI 色碼讓流程失敗 | FIXED | 讀檔後：原始位元組的 SHA → UTF-8 解碼 → 拒絕 NUL → 用 `terminal_safe_text` 把 C0／C1／bidi 跳脫成可見的形式（保留 CR、LF、tab）；新增 `control_escaped`；escape 後的長度不超過原始的 4 倍；sha256／byte_count 維持原始值 | 「讀 app/color.log」：rc=0，`control_escaped=true`，packet 正確描述資料庫連線錯誤 | **確認修好** |
| C synthesizer 太晚檢查 readiness | FIXED | `retrieve_context` 在讀取 task 與 planner 呼叫之前，先對兩個 profile 執行 `resolve_profile_config(..., require_configured=True)`；synthesizer 的憑證與 client 仍延後到讀檔之後 | `--synthesizer-provider broken`：0 秒內 rc=1，訊息為 `provider base_url is not configured`，沒有送出請求 | **確認修好** |
| D 選檔準確度 | DEFERRED | — | — | **合理**：這是只看路徑、只選一次的 MVP 限制，README 建議縮小 scope 並在 task 裡寫出檔名 |
| E `--save-output` | REJECTED FOR A10 | — | — | **合理**：在唯讀的 milestone 引入寫檔，會帶來路徑、覆寫、symlink 與資料保留的契約問題 |
| F 憑證排除清單太窄 | PARTIALLY ACCEPTED | 新增 `.config/gh`、`.config/gcloud`、`.htpasswd`、`kubeconfig`、`credentials.json`、`.cargo/credentials(.toml)`、`*.tfstate(.backup)` | — | **合理**：不直接沿用 A6 的 `sensitive_path`，因為它的 config／secret 規則太寬，會誤排除 `config.py`、`secret_manager.py` 這類原始碼 |
| G `/tmp` 錯誤訊息不清楚 | FIXED | 錯誤訊息改為 "requires physical non-symlink directories; on macOS use /private/tmp instead of /tmp" | — | **確認修好** |
| H 共用的私有 helper | DEFERRED | — | — | 可以接受，已記為技術債 |

## 剩下的問題（follow-up，不影響 merge）

| # | 嚴重度 | 問題與實測 | 建議 |
|---|--------|-----------|------|
| N1 | Low–Medium | **大檔被排除時不會通知使用者。** 「讀 app/big.log，告訴我最後的錯誤」（340,025 B）：rc=0，`retrieved_files: []`，`facts: []`，unknowns 為 "The content of app/big.log is not provided in the supplied documents."。使用者不會知道是因為超過 256 KiB 被排除，而且仍然花了 planner 與 synthesizer 兩次呼叫。README 寫明 "Regular files larger than 256 KiB are excluded, not truncated"，但執行時沒有任何提示。 | 在 stderr 與 `--json` metadata 回報被排除的候選檔案數量與原因（例如 `excluded: {oversize: 1, binary_suffix: 1}`），只回報數量，不列出路徑；可以考慮在清單為空、或 planner 選了 0 個檔案時跳過 synthesizer。 |
| N2 | Low | **二進位檔只靠副檔名判斷。** 「Inspect app/libfoo.so and app/cache.bin」：rc=1，`retrieval selected file must be UTF-8 text`，在 planner 呼叫之後才失敗。報告與 README 都已寫明這個限制。 | 在 `OBVIOUS_BINARY_SUFFIXES` 補上常見的格式：`.so`、`.dylib`、`.dll`、`.exe`、`.o`、`.a`、`.jar`、`.war`、`.class`、`.pyc`、`.whl`、`.tar`、`.tgz`、`.db`、`.bin`、`.bmp`、`.tif(f)`、`.heic`、`.wav`、`.flac`、`.iso`、`.dmg`。 |
| N3 | Low（流程） | **validation 腳本不在 repo 裡。** Codex 報告的測試與 live 指令引用了 `/private/tmp/llmtool-a10-tests.py`、`llmtool-a10-review-live.py`、`llmtool-a10-review-wheel-verify.py`、`llmtool-a10-live.py`、`llmtool-a10-wheel-verify.py`，都在 repo 外面，別人無法重現報告中的 live 與 wheel 驗證。 | 把這些腳本放進 repo（例如 `tools/llmtool/scripts/`，live 腳本預設不連網路），或在報告中註明它們是一次性的，並記錄重點步驟。 |

## 實測紀錄（7 次 gateway 呼叫）

| # | task | synthesizer | rc | 呼叫次數 | 結果 |
|---|------|-------------|----|---------|------|
| 1 | Read app/big.log and tell me the last error. | company | 0 | 2 | 沒有讀到檔案；packet 的 unknowns 說明內容沒有提供（N1） |
| 2 | Read app/color.log and explain the error. | company | 0 | 2 | `app/color.log` 45 B，`control_escaped=true`（B） |
| 3 | Read app/plain.log and explain the error. | broken | 1 | 0 | 先檢查 readiness 就失敗，沒有任何請求（C） |
| 4 | Look at app/diagram.png and app/plain.log to explain the outage. | company | 0 | 2 | PNG 被排除，只讀了 `app/plain.log`（A） |
| 5 | Inspect app/libfoo.so and app/cache.bin to find the corrupted symbol. | company | 1 | 1 | planner 之後才出現 UTF-8 錯誤（N2） |

## 清理

- 這次 review 沒有建立任何 worktree、clone 或專案副本，canonical repo 除了新增這份報告之外沒有任何變更。
- 暫存的 HOME、設定與 fixture 都在 scratchpad 的 `a10.JHy4`。
