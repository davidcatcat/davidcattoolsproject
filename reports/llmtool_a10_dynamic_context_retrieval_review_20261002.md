# llmtool A10 Dynamic Context Retrieval 獨立 review（2026-10-02）

## 範圍與方法

- 審查對象：分支 `feat/a10-dynamic-context-retrieval`，HEAD `aca7f54b3579c6253ecaf6c95fbc0e540c0a6161`。
  - 以 `origin/main` `3923f7a` 為基礎：ahead 2、behind 0。
  - 兩個 commit：`8dad54c`（實作）、`aca7f54`（validation 文件）。
  - 新增 `retrieval.py`（247 行）、`retrieval_files.py`（304 行）、`retrieval_cli.py`（25 行）、`tests/test_retrieval.py`（565 行），並更新 README 與 ROADMAP。
- 依據：ROADMAP 的 A10 段落、README 的 A10 小節，以及分支的 `reports/llmtool_a10_dynamic_context_retrieval_validation_20261002.md`。
- 方式：
  - **唯讀**，在 canonical repo `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` 進行。canonical repo 當時就 checkout 在這個分支，review 時沒有切換分支，也沒有使用 worktree、clone 或專案副本。
  - Lead 逐行閱讀 `retrieval.py`、`retrieval_files.py`、`retrieval_cli.py`。
  - CLI 以 `env -i HOME=<暫存目錄>` 執行，暫存設定只有一個公司 gateway profile `company`，planner 與 synthesizer 都使用它。
  - 唯一的網路服務是公司 gateway（`http://192.0.2.10:4000`，route `gw`，`enable_thinking: false`），共呼叫 14 次。
  - 實測對象是 llmtool 自己的原始碼與 `reports/`（唯讀），以及 scratchpad 裡的合成 fixture。

## 結論：APPROVE WITH CHANGES

**安全設計扎實，沒有找到 critical、high 或安全漏洞。** 有 3 個 medium 等級的穩健性問題（A–C）：在 planner 的呼叫已經付費之後，整個流程可能因為可以事先避免的原因而失敗。建議 merge 前修正，或明確記為 follow-up。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 597 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`，PyYAML 在使用者 site-packages） | 597 tests OK（1 skipped） |
| GitHub Actions `llmtool CI`（`aca7f54`） | 兩個 run 都 success |
| 跑完測試後 canonical repo 的工作目錄 | 沒有變更（只有原本就有的 benchmark／reasoning 未追蹤報告） |

## 安全設計（確認符合 ROADMAP）

| 要求 | 結果 | 證據 |
|------|------|------|
| root／scope 就是授權範圍；不能是絕對路徑或包含 `..` | PASS | `relative_path`（`retrieval_files.py:27-45`）拒絕絕對路徑、`..`、反斜線、磁碟代號、C0／C1／bidi／Cf 字元，並限制 4096 bytes、64 層 |
| 不跟隨 symlink，對 race 有防護 | PASS | root 的每一層都以 `os.open(..., O_DIRECTORY \| O_NOFOLLOW, dir_fd=...)` 開啟，並比對 stat 前後的 dev／ino（`_directory`，`:74-87`）；scope 不能是 symlink；symlink、FIFO、socket、裝置不會進入清單（`:212-214`） |
| 讀檔前、開檔後、讀完後都檢查身分 | PASS | `read_selected`（`:259-304`）比對 `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`：與清單比、開檔後的 fstat、讀完後的 fstat，以及路徑的 stat；位元組數必須一致；計算 SHA-256 |
| 排除 `.git` 與明顯的憑證位置 | PASS（清單較窄，見 F） | `excluded_credential_path`（`:48-63`）：`.ssh`、`.password-store`、`.aws`、`.kube`、`.docker`、`.gnupg`、`.azure`、`.config/llmtool`、`.env*`、`.netrc`、`.npmrc`、`.pypirc`、`.pgpass`、`.git-credentials`、`.vault-token`、`id_*`、`*.pem/.key/.p12/.pfx`、`/etc/shadow`、`/etc/gshadow`、`/proc/*/environ` |
| planner 只看到 task 與路徑／大小，只能選清單內的路徑 | PASS | `retrieve_context`（`retrieval.py:223-226`）；`parse_retrieval_plan`（`:72-127`）只接受 strict JSON `{"files":[...]}`、深度不超過 2、沒有重複 key／constant、最多 8 個且不能重複、必須在清單內，**整個驗證完才讀取內容** |
| synthesizer 才看到內容；evidence 的 source 必須是 `task` 或實際讀到的路徑 | PASS | `ContextRetrievalResult.__post_init__`（`:171-197`）：重用 A4 的 `parse_context_packet` 與 A6 的 `validate_context_packet`，檢查 source 屬於 `{"task", *paths}`、檔案在 scope 內、不重複、總量上限 |
| 成功時剛好兩次 provider 呼叫；任何失敗就停止，沒有 retry、fallback 或遞迴 | PASS | 程式流程與實測一致 |
| profile 只讀存起來的設定，不受 `LLMTOOL_*` 或專案設定影響；先檢查 readiness 再讀憑證 | PASS（時機見 C） | 使用 `resolve_profile_config` |
| 沒有 shell、Git、工具、寫檔、patch 或執行指令 | PASS | 新模組只做唯讀的 `os.open`、`scandir`、`stat`；CLI 只輸出到 stdout |
| `--json` 不包含原始檔案內容 | PASS | `to_dict` 只有路徑、byte_count、sha256、runtime、packet |

## 公司 gateway 實測

| # | 指令（`--root` 是 canonical repo，除非另外註明） | rc | 時間 | 結果 |
|---|------|----|------|------|
| 1 | scope `tools/llmtool/llmtool`：「analyze-log 的 NEXT_COMMANDS 過濾在哪裡實作、會擋哪些建議？」 | 0 | 4s | 只選了 `skills/analyze-log/SKILL.md`（1378 B）。**漏掉真正的實作 `log_safety.py`**，packet 只能從 prompt 規則推論，並把「是否有其他機制」列為未知 |
| 2 | scope `tools/llmtool`：同一個問題，要求涵蓋 validator、測試、文件 | 0 | 9s | 選了 `SKILL.md`、`validator.py`、`tests/test_validator.py`（共 49,538 B）。**仍然漏掉 `log_safety.py`** |
| 3 | scope `tools/llmtool`：「總結 A0–A10 並指出程式與測試」 | 0 | 18s | 選了 8 個檔案（ROADMAP、README 96,780 B、cli、config、retrieval、retrieval_files、orchestration、validator），共 212,932 B |
| 4 | scope `reports`：「比較所有 llmtool 報告並列出未解決的問題」 | 0 | 8s | 選了 8 個報告，共 104,581 B |
| 5 | `--root /private/tmp/<probe>` 的小檔案 | **1** | — | 兩次呼叫都花了，但 synthesizer 的輸出違反 `context packet JSON exceeds nesting limit`，整個失敗 |

### 刻意製造的失敗（scratchpad 裡的合成 fixture）

fixture 內容：`app/big.log`（340,025 B）、`app/diagram.png`（二進位）、`app/color.log`（帶 ANSI 色碼）、`app/plain.log`。

| task | 結果 |
|------|------|
| 「讀 app/big.log，告訴我最後的錯誤」 | rc=1：`retrieval selected file exceeds 256 KiB`（**planner 已呼叫**） |
| 「看 app/diagram.png 與 app/plain.log 說明 outage」 | rc=1：`retrieval selected file must be UTF-8 text`（**planner 已呼叫**） |
| 「讀 app/color.log 說明錯誤」 | rc=1：`retrieval evidence contains unsafe text controls`（**planner 已呼叫**） |
| synthesizer 用未設定完成的 profile `broken` | rc=1：`provider base_url is not configured`，**在 planner 呼叫與讀檔之後**才失敗 |
| `--root /tmp/<probe>`（macOS 的 `/tmp` 是 symlink） | rc=1：`retrieval directory must be real and non-symlink`（README 有寫要改用 `/private/tmp`） |

## 問題清單

| # | 嚴重度 | 位置 | 問題 | 建議修正 |
|---|--------|------|------|---------|
| A | Medium | `retrieval_files.py:175-232`、`retrieval.py:28-34` | **planner 不知道限制，選錯就整個失敗。** 清單是純 metadata，包含超過 256 KiB 的檔案與二進位檔；`PLANNER_SYSTEM` 也沒有提到單檔 256 KiB、總共 512 KiB 的上限。實測選到大檔或 PNG，都在 planner 呼叫之後 rc=1。 | 清單排除（或標記為不可讀）超過 `MAX_FILE_BYTES` 的檔案與明顯的二進位檔（例如前幾 KiB 含 NUL），並在 planner 的 prompt 寫明上限；或者把不可讀的選擇當成被跳過的項目回報，而不是讓整個流程失敗。 |
| B | Medium | `retrieval_files.py:249-250` | **含 ANSI 色碼或其他控制字元的文字檔讓整個流程失敗。** 帶顏色的 log 很常見。 | 把控制字元跳脫成可見的形式（例如 `\x1b`）再交給 synthesizer，並在 metadata 註明；而不是直接拒絕。 |
| C | Medium | `retrieval.py:220,228` | **synthesizer 的 profile 要等 planner 呼叫並讀完檔案之後才 resolve。** 設定錯誤的 synthesizer 會浪費 planner 的付費呼叫（和 A9 先前的 L4 同一類）。 | 在任何請求之前，先對兩個 profile 執行 `resolve_profile_config(..., require_configured=True)`，只檢查 readiness，不讀憑證。 |
| D | Low | 設計 | **選檔的準確度。** 兩次和 analyze-log 相關的實測，都沒有選到真正的實作 `log_safety.py`。這是「只選一次、只看檔名」的設計限制，ROADMAP 已註明「Path selection may miss relevant evidence」，但實際影響很明顯。 | 在 README 提醒使用者縮小 scope 或把相關檔案列在 task 裡；之後可以考慮在清單裡附上有上限的檔案開頭，或允許使用者明確指定必讀的檔案。 |
| E | Low | `retrieval.py:233` | **synthesizer 的輸出稍微不合格，兩次呼叫就都浪費了**（實測：nesting limit），而且失敗時沒有任何可以診斷的資料。 | 提供明確 opt-in 的 `--save-output FILE`，只寫到使用者指定的位置。 |
| F | Low | `retrieval_files.py:48-63` | **憑證的排除清單比較窄。** 沒有涵蓋 `~/.config/gh/hosts.yml`（GitHub token）、`*.tfstate`、`credentials.json`、`.htpasswd`、`.cargo/credentials`、`.config/gcloud/`、service account 的 JSON、`kubeconfig` 這類檔名。README 已註明排除清單不能保證沒有 secret。 | 補上常見的位置，並讓 A10 與 A6 的 `sensitive_path` 共用同一份清單。 |
| G | Low | `retrieval_files.py:76-77` | **macOS 上 `--root /tmp/...` 被拒絕**，錯誤訊息只說 "must be real and non-symlink"。 | 錯誤訊息提示要改用實體路徑（例如 `/private/tmp`）。 |
| H | Nit | `retrieval.py:7-8` | **架構：** 重用 `benchmark.normalize_runtime` 與私有的 `config._provider_name`，和 A9 先前的 L8 是同一類問題。 | 抽成共用的 runtime／名稱驗證模組。 |

## 建議

1. merge 前修正 **A、B、C**：把限制告訴 planner 並過濾掉不可讀的候選檔案、跳脫控制字元、在送出請求前先檢查兩個 profile。每一項都加上 regression 測試，最好包含用公司 gateway 實際跑的案例。
2. **D–H** 記為 follow-up。
3. 修正後，用公司 gateway 再跑一次上方的實測與刻意製造的失敗。

## 清理

- 這次 review 沒有建立任何 worktree、clone 或專案副本，也沒有修改 canonical repo 的檔案（除了新增這份報告）。
- 暫存的 HOME、設定與 fixture 都在 scratchpad 的 `a10.JHy4`。
- 測試時在 `/tmp` 建立的探測目錄已經移除。
