# llmtool post-A9 hardening 分支 re-review（2026-10-02）

## 範圍與方法

- 審查對象：分支 `fix/post-a9-hardening`，HEAD `63c33b90d00475bbeb0c0a59e0be6e3af64e40e9`。
  - 相對 `origin/main` `f5001b5`：ahead 5、behind 0。
  - 本輪新增的 commit：`41b81c2`（同步 main）、`a036183`（修正 review 找到的 regression）、`63c33b9`（validation 文件）。
- 對照：上一輪的 [post-A9 hardening review](llmtool_post_a9_hardening_review_20261001.md)（R1–R8，結論 REQUEST CHANGES）。
- 方式：
  - **唯讀**，在 canonical repo `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` 進行。canonical repo 當時就 checkout 在這個分支，review 時沒有切換分支，也沒有使用 worktree、clone 或專案副本。
  - CLI 一律以 `env -i HOME=<暫存目錄>` 執行，沒有讀取真實的憑證或設定。
  - 唯一的網路服務是公司 gateway（`http://home-llm.example:8000`，route `gw`，`enable_thinking: false`），共呼叫 10 次。

## 結論：APPROVE（可以 merge），並建議 1 項 follow-up

上一輪的 R1–R7 都已修好。R1 用公司 gateway 實測確認：原本 4 個 log 有 3 個失敗，現在 4 個都是 rc=0，分析內容都保留下來。剩下 A6 的敏感路徑清單有幾個缺口，建議記為 follow-up。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 549 tests OK（1 skipped） |
| Python 3.9.6（隔離的 venv） | 549 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`，PyYAML 在使用者 site-packages） | 549 tests OK（1 skipped） |
| GitHub Actions `llmtool CI`（`63c33b9`） | `36893881723`、`36893887183` 都 success |
| 跑完測試後 canonical repo 的工作目錄 | 沒有變更，只有原本就存在的 benchmark／reasoning 未追蹤報告 |

## 公司 gateway 實測：analyze-log（與上一輪相同的 4 個 log）

| log | 上一輪 | 這一輪 | NEXT_COMMANDS |
|-----|--------|--------|---------------|
| 資料庫認證失敗 | rc=0，但建議 `grep -r "password" CONFIG_FILE_PATH`、`cat CONFIG_FILE_PATH` | **rc=0**；stderr：`suppressed 2 unsafe or unsupported NEXT_COMMANDS suggestion(s)` | 2 行 `[suppressed unsafe or unsupported suggestion]`，1 行 `echo "Verify database user 'app' password matches application configuration"` |
| OOM killer | rc=1，output omitted | **rc=0** | `journalctl -u app.service --since … --until …`、`free -m`、`cat /proc/4412/status` |
| Kubernetes（BackOff、缺少 Secret） | rc=1，output omitted | **rc=0**；suppressed 1 | `[suppressed …]`（`get secret`）、`kubectl describe pod api-7d9f-xk2 -n prod`、`kubectl get events -n prod --sort-by='.lastTimestamp'` |
| nginx upstream refused | rc=1，output omitted | **rc=0** | `ss -tlnp \| grep :8080`、`systemctl status SERVICE_NAME`、`journalctl -u SERVICE_NAME --since … --until …` |

每一份輸出都保留了 `ROOT_CAUSE` 等分析段落。

## R1–R8 驗證

| # | 結果 | 證據 |
|---|------|------|
| R1 analyze-log 整份回答被丟掉 | **FIXED** | 見上方實測。`filter_log_suggestions` 只會把有問題的建議換成 `[suppressed unsafe or unsupported suggestion]`，並在 stderr 回報擋掉的數量。誤判也修好了：`ls -la /usr/bin/python3`、`kubectl logs POD -c python`、`grep -n sh app.log`、`systemctl status nginx`、`kubectl describe pod p`、`journalctl -u SERVICE_NAME -n 50`、`tail -n 50 /var/log/app.log` 都會保留。 |
| R2 stdin 被關閉時 | **FIXED** | `llmtool ask 'reply OK only' <&-` 回傳 rc=0（上一輪是 rc=1）。 |
| R3 `while read` 迴圈／普通檔案作為 stdin | **FIXED** | 對兩行的檔案跑 `while read q; do llmtool ask "$q"; done < qs`，兩次都是 rc=0。現在只有 FIFO／socket 會用 FIONREAD 檢查，其他的 stream 與非 POSIX 環境都保留明確給的輸入。 |
| M2 pipe 裡已經有資料時 | 仍然有效 | `echo hi \| llmtool ask q` 回傳 rc=1，訊息為 "provide only one of …"。 |
| R4 顯示憑證的繞過方式 | **FIXED** | 上一輪列出的指令都會被擋：`kubectl get --raw /api/v1/namespaces/NS/secrets/NAME`、`oc get secrets -o yaml`、`echo secrets \| xargs kubectl get -o yaml`、`kubectl get pod POD -o jsonpath='{…env}'`、`kubectl exec POD -- env`、`od -c ~/.ssh/id_rsa`、`git config --list`、`helm get values R`、`vault kv get secret/x`、`aws secretsmanager get-secret-value`、`kubectl --server https://evil get pods`、`cat CONFIG_FILE_PATH`、`grep -r password CONFIG_FILE_PATH`、`cat .env`、`printenv`、`kubectl get secret NAME -n NS`。 |
| R5 A4 拒絕 Unicode format 字元 | **FIXED** | `context._text` 接受 ZWJ emoji、soft hyphen、BOM、ZWSP，但仍然拒絕 bidi（U+202E）、ESC、C1（U+0085）。這也修好了更早的 L2（C1 字元）。 |
| R6 分層與重複 | **FIXED** | Secret 與敏感路徑的規則集中在 `validator.py` 的公開函式 `sensitive_path`、`secret_resource`、`credential_display`；`log_safety.py` 只 import 公開 API。 |
| R7 先讀 stdin 才檢查設定 | **FIXED** | `cli.py:173` 先做 `validate_input_sources`（只檢查、不讀取），`cli.py:174` 執行 `resolve_config`，`cli.py:180` 才 `read_input`。 |
| R8 文件 | 已更新 | README 與 validation report 都有更新（沒有逐句核對）。 |

## 剩下的問題（建議 follow-up）

**F1（Medium）：A6 的 `sensitive_path` 沒有涵蓋幾個常見的機密來源。** 在 analyze-log 的過濾中，以下指令會**保留**，validator 也只判為 UNKNOWN：

| 指令 | 內容 |
|------|------|
| `cat /etc/shadow`、`sudo cat /etc/shadow`、`head -n 20 /etc/shadow` | 系統密碼雜湊 |
| `cat /var/lib/postgresql/.pgpass` | PostgreSQL 密碼檔 |
| `cat /proc/1/environ`、`strings /proc/1234/environ` | process 環境變數（常含 token） |
| `set` | 所有 shell 變數 |

已經會被擋的常見憑證來源：`~/.ssh/id_*`、`~/.aws/credentials`、`~/.kube/config`、`~/.netrc`、`~/.docker/config.json`、`~/.git-credentials`、`/etc/ssl/private/*`、`~/.config/llmtool/config.yaml`、`/etc/company/app/config.yaml`、`env`、`printenv`。

建議：把 `/etc/shadow`、`/etc/gshadow`、`.pgpass`、`/proc/*/environ` 加進 `sensitive_path`，並把 `set` 與 `env`／`printenv` 視為同一類，各補上 regression 測試。

**F2（Nit）：** 角括號形式的 placeholder（例如 `kubectl logs <pod> -n <ns>`）仍然會被擋。本輪 SKILL.md 改為要求使用 `POD_NAME` 這類大寫 placeholder，實測時模型也照做了，所以影響很小。

## 清理

- 這次 review 沒有建立任何 worktree、clone 或專案副本。
- 之前留下的 scratchpad worktree（`main-docs`，佔用 `main`）已經用 `git worktree remove` 移除。移除前確認過它的內容等於 `5cecbd6`，而這個 commit 已經在 `origin/main` 的歷史裡，沒有遺失任何東西。
- scratchpad 的暫存資料（專案副本、測試用 repo、暫存 HOME 目錄、Python 3.9 venv）已經全部清除。
- `git worktree list` 只剩 canonical repo 本身。
