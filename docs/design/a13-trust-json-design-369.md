# A13 trust.json design (source: #369 comment 6552692881)

Copied verbatim from https://github.com/davidcatcat/davidcattoolsproject/issues/369#issuecomment-6552692881 so Developers can read it offline. The issue comment remains the reviewed source; this copy adds no authority.

> 這是 READ-ONLY 調查產出的設計規格，**不是實作**。它依據 #396 上 Owner 於 2026-11-01 做的決定 D1–D8，並配合同一處的 SF-2（審查端改為同時接受 SSH 與 HTTPS origin）。
>
> 規格需要 Coordinator／Owner 審定。§12 的待決問題，會另外整理成白話版再請 Owner 決定。
>
> 實作要經 PM 派工給允許的 Developer 路線，再由另一個模型家族的 Reviewer 正式審查。`claude-developer` 目前 PAUSED，所以 Cloud 不實作。

# Task Contract／設計規格：Owner-only `trust.json`（取代寫死的信任錨點，issue #369）

- Base commit：`e1387d896d8e6d352b9b9952e17b6181b7b88439`
- 範圍：`tools/llmtool`、`skills/safe-git/preflight.py`
- 標記：**FACT** = 已在 base commit 原始碼中核對；**OPINION** = 設計建議，需 Coordinator／Owner 確認
- 佔位符：`<OWNER_HOME>`、`<CANONICAL_ROOT>`、`<WORKSPACE_ROOT>`、`<TASKS_ROOT>`、`<OWNER_REPO>`（`owner/name` slug）、`<RUNNER_MAC_*>`、`<RUNNER_LINUX_*>`
- 原型：在 Linux 上驗證過 dirfd 走訪讀取演算法可以實作，14 個正負案例都回傳預期的錯誤碼。原型沒有加入 repo。macOS 尚未驗證，不宣稱 Mac 上的行為。

---

## 0. 背景與目標

**FACT**：信任錨點目前寫死在原始碼裡，包括：
- Owner 的絕對路徑；
- `<OWNER_REPO>` slug 與 origin URL；
- issue 與 PR 編號；
- CI runner 名稱、workflow 路徑與 check 名稱。

因此其他安裝環境的使用者，必須修改受保護的原始碼，才能使用 D6 worktree、Developer／Reviewer 派工與 CI 驗證。

**目標**：每個安裝環境的使用者，就是那個安裝的 Owner。信任錨點改從一個 owner-only 的 `trust.json` 讀取，原則如下：
1. 檔案缺失或無效時一律 fail closed，回傳 sanitized code，**不得**靜默退回寫死的常數。
2. Owner 目前的 Mac 只需執行一次 `llmtool trust init`，就能以**完全相同的值**繼續運作。
3. 只支援 POSIX；Windows 回傳 `TRUST_UNSUPPORTED_PLATFORM`。
4. 只支援 GitHub Actions；CI 模式由使用者在 `github-hosted` 與 `self-hosted` 之間二選一。
5. `LLMTOOL_D6_TESTING` 維持只供測試使用，語意不變，**不得**用來繞過 trust 讀取，也不得用來指定 trust 的位置。

非目標：非 GitHub 的 CI、Windows、多個 repository、遠端或共享的 trust、修改 D6／D8 授權模型本身。

---

## 1. 檔案位置與讀取演算法

### 1.1 位置規則
- 路徑：`P = pw_dir + "/.config/llmtool/trust.json"`，其中 `pw_dir = pwd.getpwuid(os.getuid()).pw_dir`。
- **不**讀 `$HOME`、`XDG_CONFIG_HOME`、`LLMTOOL_HOME` 或任何環境變數，也不提供 CLI 覆寫參數。
  - **OPINION**：理由是 `$HOME` 可以被子程序的 env 改寫。
  - **FACT**：子程序的 env 確實會設定 `HOME`，見 `task_submission_cli.py:1778,1878`、`d6_worktree_lifecycle.py:221,350`。
- 測試可以透過模組內部參數 `home=` 傳入路徑，但不可經由 CLI 或環境變數。
- 前置檢查：
  - `os.name == "posix"` 且 `sys.platform` 不以 `win` 開頭，否則回傳 `TRUST_UNSUPPORTED_PLATFORM`。
  - `os.getuid() == os.geteuid()`，否則回傳 `TRUST_IDENTITY_INVALID`。
  - `euid == 0` 時拒絕，回傳 `TRUST_IDENTITY_INVALID`（**OPINION**，見 Q7）。
  - `pw_dir` 必須是絕對路徑，且 `os.path.normpath(pw_dir) == pw_dir == os.path.realpath(pw_dir)`，否則回傳 `TRUST_HOME_INVALID`。

### 1.2 讀取演算法（`read_trust_bytes`）
原型已驗證。參考既有先例：`test_triage_worker.py:171-244`（`_grant_outside_worktree`、`load_phase0_grant`）與 `remote_test.py:222-246`。

1. `fd = open("/", O_RDONLY|O_DIRECTORY)`，`fstat` 後套用祖先規則 A。
2. 對 `pw_dir` 的每個路徑元件，以及 `.config`、`llmtool`，逐層執行 `openat(fd, name, O_RDONLY|O_DIRECTORY|O_NOFOLLOW)`：
   - 遇到 `ENOENT` 回傳 `TRUST_MISSING`；遇到 `ELOOP`、`ENOTDIR` 等其他錯誤回傳 `TRUST_ANCESTOR_INVALID`。
   - 每一層都對新的 fd 做 `fstat`，並套用以下規則：
     - **祖先規則 A**（`/` 到 `<OWNER_HOME>/.config`）：必須是 `S_ISDIR`；`st_uid ∈ {0, euid}`；不可被 group 或 other 寫入，唯一例外是 `uid==0 && S_ISVTX`。
     - **規則 B**（`<OWNER_HOME>/.config/llmtool`）：`st_uid == euid` 且 `S_IMODE == 0o700`，否則回傳 `TRUST_DIRECTORY_NOT_PRIVATE`。
   - 每一層都執行 `fstatat(fd, ".git", AT_SYMLINK_NOFOLLOW)`。只要 `.git` 存在（目錄或檔案都算），就回傳 `TRUST_INSIDE_WORKTREE`。
3. 執行 `openat(dirfd, "trust.json", O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)`：
   - `ENOENT` 回傳 `TRUST_MISSING`；symlink、FIFO 等開啟失敗的情況回傳 `TRUST_FILE_NOT_PRIVATE`。
4. `fstat` 必須同時滿足 `S_ISREG`、`st_uid == euid`、`S_IMODE == 0o600`、`st_nlink == 1`，否則回傳 `TRUST_FILE_NOT_PRIVATE`。另外 `st_size` 必須介於 1 到 16384，否則回傳 `TRUST_TOO_LARGE`。
5. 執行 `read(fd, 16385)`。接著比對三份 metadata 的 `(dev, ino, uid, mode, nlink, size, mtime_ns, ctime_ns)` 是否一致：
   - 開檔後的 `fstat`；
   - 讀取後的 `fstat`；
   - `fstatat(dirfd, name, NOFOLLOW)`。

   另外必須 `len(raw) == size`。任一項不符就回傳 `TRUST_CHANGED_DURING_READ`。
6. 計算 `trust_sha256 = sha256(raw)`，然後關閉所有 fd。
7. 解析 JSON：
   - 開頭有 UTF-8 BOM 時拒絕；
   - `raw.decode("utf-8")` 必須以嚴格模式成功；
   - 使用 `json.loads(..., object_pairs_hook=<遇到重複 key 即拒絕>, parse_constant=<拒絕 NaN/Infinity>)`；
   - `RecursionError`、`ValueError`、`UnicodeError` 一律回傳 `TRUST_MALFORMED`。
8. 依第 2 節驗證 schema：每一層物件的 key 集合必須**完全相等**，不允許多出的 key；`bool` 不可當作 `int`。
9. 依第 3 節檢查 load-time invariants。
10. 回傳 frozen 的 `TrustConfig`（dataclass，`frozen=True`，容器一律用 tuple 或 frozenset），附帶 `sha256` 與 `binding_sha256`（見 7.2）。

**OPINION**：macOS 的 ACL 檢查可以比照 `skills/safe-git/preflight.py:52-` 的 `ls -lde` 先例。PR-1 不做，列為 Q8。

---

## 2. JSON Schema（`schema_version: 1`）

```json
{
  "schema_version": 1,
  "canonical_root": "<CANONICAL_ROOT>",
  "workspace_root": "<WORKSPACE_ROOT>",
  "tasks_root": "<TASKS_ROOT>",
  "repository": {
    "slug": "<OWNER_REPO>",
    "allowed_origins": ["git@github.com:<OWNER_REPO>.git", "https://github.com/<OWNER_REPO>.git"],
    "approval_refs": {
      "reviewer_host_policy_issue": 120,
      "worker_dispatch_issue": 289,
      "worker_dispatch_evidence_pr": 283,
      "retain_release_issues": [289, 352]
    }
  },
  "ci_trust": {
    "mode": "self-hosted",
    "github_actions_app_id": 15368,
    "workflows": [
      {"path": ".github/workflows/llmtool-mac-ci.yml", "name": "llmtool Mac CI",
       "runner_names": ["<RUNNER_MAC_1>", "<RUNNER_MAC_2>"], "runner_labels": null,
       "code_check": "check (macOS 3.13)", "docs_check": null},
      {"path": ".github/workflows/llmtool-linux-ci.yml", "name": "llmtool Linux CI",
       "runner_names": ["<RUNNER_LINUX_1>", "<RUNNER_LINUX_2>", "<RUNNER_LINUX_3>"], "runner_labels": null,
       "code_check": "check (Linux 3.14)", "docs_check": "check (docs)"}
    ],
    "not_run_checks": ["Linux Python 3.9", "Windows CI"]
  },
  "tools": {"git": null, "gh_path": null, "lsof": null}
}
```

### 2.1 欄位約束

| 欄位 | 型別與約束 |
|---|---|
| `schema_version` | `int`，必須為 `1`；其他值回傳 `TRUST_SCHEMA_VERSION_UNSUPPORTED` |
| `canonical_root` | `str`，長度 ≤ 1024，絕對路徑，只能是 ASCII 可列印字元、不含控制字元；必須 `normpath(x)==x==realpath(x)`、是目錄、`st_uid==euid`、不可被 group 或 other 寫入 |
| `workspace_root` | 約束同上。這是所有 Developer worktree 的父目錄，現值是 tasks、codex、claude 三者的共同父目錄 |
| `tasks_root` | 約束同上；必須是 `workspace_root` 的嚴格子孫；`S_IMODE` 不可含 `0o022` |
| `repository.slug` | 沿用 `config.py:137-146` 的 regex `[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}`；repo 名稱部分不可為 `.` 或 `..`（FACT） |
| `repository.allowed_origins` | 長度 1–2、元素不重複的 `str` list。每一項必須**字面相等**於 `git@github.com:<slug>.git` 或 `https://github.com/<slug>.git`，不接受其他形式 |
| `approval_refs.*_issue`、`*_pr` | `int`，範圍 `1..10**9`，不可為 bool |
| `approval_refs.retain_release_issues` | 長度 1–8、元素不重複的 `int` list，範圍同上 |
| `ci_trust.mode` | `"github-hosted"` 或 `"self-hosted"` |
| `ci_trust.github_actions_app_id` | `int`，必須等於 `15368`，**不**接受其他值。這是 GitHub Actions 的全域 app id（FACT，見 `task_completion.py:61,1790`），寫進檔案只是為了明確 |
| `ci_trust.workflows` | 長度 1–4 的 list，`path` 不可重複 |
| `workflows[].path` | 必須符合 `\.github/workflows/[A-Za-z0-9._-]{1,100}\.ya?ml` |
| `workflows[].name` | `str`，長度 1–100，不含控制字元 |
| `workflows[].runner_names` | `self-hosted` 模式：長度 1–16 的唯一 `str` list，每一項符合 `[A-Za-z0-9._-]{1,64}`；不允許 `*`、`?`、`[` 等萬用字元，以精確比對判斷。`github-hosted` 模式：必須為 `null` |
| `workflows[].runner_labels` | `github-hosted` 模式：長度 1–8 的唯一 list，每一項符合 `[A-Za-z0-9._-]{1,64}`，且**不可**包含 `self-hosted`。`self-hosted` 模式：可為 `null`，或為一份精確清單 |
| `workflows[].code_check`、`docs_check` | `str`（長度 1–100，不含控制字元）或 `null`。整份設定中至少要有一個非 null 的 `code_check` 和一個非 null 的 `docs_check`；所有 check 名稱彼此不可重複 |
| `ci_trust.not_run_checks` | 長度 0–8 的唯一 `str` list，只用於顯示（FACT：用於 `task_completion.py:2101`） |
| `tools.git` | `null` 或絕對路徑字串。`null` 代表維持現行的固定路徑 `/usr/bin/git`（FACT：`task_completion.py:827`、`d6_worktree_lifecycle.py:229` 等） |
| `tools.gh_path` | `null`，或由 1–6 個以 `:` 分隔的絕對目錄組成的 PATH 字串。`null` 代表維持現行的 `_GH_PATH`（`task_completion.py:68`） |
| `tools.lsof` | `null` 或絕對路徑字串 |

`tools` 中非 null 的路徑必須同時符合以下條件，否則回傳 `TRUST_TOOL_INVALID`：
- 是 physical path（realpath 等於自身）；
- 是可執行的 regular file；
- `st_uid ∈ {0, euid}`，且不可被 group 或 other 寫入；
- 所有祖先都符合規則 A。

**OPINION**：`tools` 為 `null` 時，保留既有寫死的**工具**路徑。工具路徑不是信任錨點，所以這不違反「不得靜默退回寫死常數」；該原則只適用於本節的身分錨點。

### 2.2 approval issue 盤點（FACT）

| 寫死位置 | 編號 | 用途 | 對應的新欄位 |
|---|---|---|---|
| `review_authority.py:31` | issue #120 | Reviewer host policy 的證據 comment URL | `reviewer_host_policy_issue` |
| `config.py:34`；`worker_dispatch.py:72-78` | issue #289 | worker 派工證據 | `worker_dispatch_issue` |
| `config.py:35`；`worker_dispatch.py:73-78` | PR #283 | 派工證據 PR | `worker_dispatch_evidence_pr` |
| `d6_worktree_lifecycle.py:1750-1752`（另見 help 文字 :1888） | issue #289、#352 | `--release-retain` 的 Owner 核准 URL | `retain_release_issues` |
| `task_completion.py:52`（只在註解中） | issue #99 | runner 來源說明 | 不需要對應欄位 |

其他使用 slug 的 URL 樣式（FACT），一律改由 `repository.slug` 產生：
- `review_bundle.py:128`：`actions/runs/`
- `chat_tui_status.py:16`：`pull/`
- `task_completion.py:1620`：`gh api repos/<slug>/`
- `task_completion.py:1760`：`api.github.com/repos/<slug>/check-runs/`
- `task_completion.py:1732,1858`、`task_review_collector.py:221,391`、`review_authority.py:291`、`review_exchange.py:166`：`full_name` 或 `repository` 欄位的相等比較

---

## 3. Load-time invariants

### 3.1 純檔案系統層（在 `load_trust()` 內執行，不呼叫 git）
1. `canonical_root`、`workspace_root`、`tasks_root` 都必須已存在，且是 physical path（realpath 等於自身），否則回傳 `TRUST_PATH_INVALID`。
2. `canonical_root` 與 `workspace_root` 不可相等，也不可互為祖先或子孫，否則回傳 `TRUST_PATHS_OVERLAP`。
3. `tasks_root` 必須是 `workspace_root` 的嚴格子孫。
4. trust 目錄 `<OWNER_HOME>/.config/llmtool` 與三個 root 不可互為祖先或子孫，否則回傳 `TRUST_PATHS_OVERLAP`。另外三個 root 都不可等於 `<OWNER_HOME>`。
5. trust 檔的所有祖先都不含 `.git`（見 1.2 第 2 步），因此 trust.json 不會位於任何 worktree 之內，包括可寫的 Developer worktree。
6. 第 2 節的跨欄位約束：origin 與 slug 一致、check 名稱唯一、模式與 runner 欄位相符。

### 3.2 Repository 層（`verify_repository(trust)`，在 consumer 入口執行）
**OPINION**：拆成獨立函式，讓 PR-1 保持純粹、不依賴 subprocess；consumer 在既有的 git 檢查處呼叫即可。
1. `git -C <canonical_root> rev-parse --show-toplevel` 必須等於 `canonical_root`。
2. `--path-format=absolute --git-common-dir` 和 `--absolute-git-dir` 都必須等於 `canonical_root + "/.git"`。這確保 canonical_root 是主 worktree，而不是 linked worktree。
3. `remote get-url origin` 必須屬於 `allowed_origins`。
4. 以上任一項不符就回傳 `TRUST_REPOSITORY_INVALID`。
5. git 指令沿用現行的 hardened env，以及 `-c core.hooksPath=/dev/null` 等參數（FACT 模式，見 `d6_worktree_lifecycle.py:220-232`）。

---

## 4. 架構限制與模組配置

**FACT**：有些腳本在隔離的子程序中執行，無法 import 套件 `llmtool`：
- `d6_worktree_lifecycle.py` 和 `d6_worktree_env.py` 以 `python -B -I -c <source>` 執行（`d6_worktree_lifecycle.py:345-358`、`task_submission_cli.py:1883-1887`）。
- `skills/safe-git/preflight.py` 是只用 stdlib 的獨立腳本。

**OPINION**：配置方案如下。
- `tools/llmtool/llmtool/trust.py` 是 canonical 實作，只用 stdlib，提供 `load_trust()`、`verify_repository()`、`TrustConfig` 和 `TrustError(code)`。
- 上述三支腳本各自內嵌一段 reader 區塊，以 `# BEGIN llmtool-trust-reader v1` 和 `# END llmtool-trust-reader v1` 標記。內容必須與 `trust.py` 中同樣標記的區塊**逐位元組相同**，由測試強制檢查。
- 子程序必須收到父程序傳入的 `--trust-sha256 <hex>`，並在自己讀取 trust.json 後比對，不符時回傳 `TRUST_CHANGED_DURING_RUN`。
- 替代方案是把 trust 的 bytes 經 stdin 傳給子程序。這樣仍然需要複製驗證器，因此不採用（見 Q3）。

---

## 5. PR 切分（依序執行，各自獨立、可回退）

**Owner 前置作業：** PR-2 merge 後，Owner 要在 Mac 上執行 `llmtool trust init` 和 `llmtool trust verify-legacy`，結果必須全部是 `MATCH`，PR-3 才能 merge。從 PR-3 開始，沒有 trust.json 的環境會 fail closed。

| PR | 內容 | 影響範圍 |
|---|---|---|
| **PR-1** | 只新增 loader：`llmtool/trust.py` 和測試，**完全不接線** | 新增 2 個檔案 |
| **PR-2** | CLI：`llmtool trust init`、`trust show`，以及過渡用的 `trust verify-legacy`；不接線 | `cli.py`，新增 `trust_cli.py` |
| **PR-3** | D6 lifecycle 與 env helper 接線 | 見 5.1 |
| **PR-4** | D6 registry 寫入 trust 戳記，並處理 rotation | `d6_worktree_lifecycle.py` |
| **PR-5** | Dispatcher：`task_submission_cli.py` | 見 5.1 |
| **PR-6** | Review 路徑：slug、origin、#120 | 見 5.1 |
| **PR-7** | Deny-list 與保護路徑 | 見 5.1 |
| **PR-8** | CI 信任：`task_completion.py`，支援兩種模式 | 見 5.1 |
| **PR-9** | Repository 設定：`config.repository_config`、`worker_dispatch`、`chat_tui_status` | 見 5.1 |
| **PR-10** | 清理：刪除所有遺留常數與 `verify-legacy`，新增 guard 測試 | 全域 |

### 5.1 各 PR 要改的呼叫點（FACT 位置）

**PR-3（D6 lifecycle 與 env helper）**
- `scripts/d6_worktree_lifecycle.py:27-30` 的 `TASKS_ROOT` 改為 `trust.tasks_root`。
- `:33-36` 的 `ORIGINS` 改為 `trust.repository.allowed_origins`。使用處：`:249,429,1377,1546,1669,1788`。
- `:37-38` 的 `D6_INTEGRATOR_DISPATCHER_ROOT` 改為 `trust.canonical_root`。使用處：`:218`、`:1996`，以及第二個 resume 分支中的同一比較。
- 函式預設值 `workspace_root=TASKS_ROOT`（`:394,575,705,1518,1656`）改為 `None`，在函式內解析成 trust 值。
- 相等檢查（`:419,579,712,1366,1535,1659,1778`）改成「`workspace_root != trust.tasks_root` 且未設定 `LLMTOOL_D6_TESTING`」，語意不變。
- `--repo-root` 的預設值（`:1898`）改為 `None`，未指定時由 trust 解析。
- 錯誤處理：
  - `_main` 補上 `except TrustError`，輸出 `STOP: D6 lifecycle rejected (<TRUST_*>)` 並以 exit 1 結束。
  - `resolve(strict=True)` 拋出的 `FileNotFoundError`（例如 `:421`）包裝成 `D6_REPOSITORY_INVALID`，**不再印出 traceback 和路徑**。
- 對 lifecycle 的子程序呼叫（`:345-358`，以及從 `:373` 開始的 `_bootstrap`）追加 `--trust-sha256`。
- 嵌入第 4 節的 reader 區塊。
- `scripts/d6_worktree_env.py:35-38` 的 `_EXPECTED_ORIGINS`（使用處 `:435`）改為 trust 值，同樣嵌入 reader 區塊。
- **OPINION（Q5）**：`create_worktree` 目前只檢查 `--repo-root` 的 git 狀態和 origin，沒有和 dispatcher root 比對是否相等（FACT：`:421-430`）。建議在非測試模式下，加上 `canonical_root == trust.canonical_root` 的檢查。

**PR-4（registry 戳記）**
- 在 `create_worktree` 建立 record 的地方（`:468-488`）新增 `trust_sha256` 和 `trust_binding_sha256`。
- 以下操作都要讀取並比對這兩個值：
  - `record_result`（從 `:575` 開始）
  - `bind_native_session`（從 `:705` 開始）
  - retire plan（`:1096-1200`）
  - `rebind_device_tasks`（從 `:1360` 開始）
  - `release_retained_records`（從 `:1518` 開始）
  - `reconcile_registry`（從 `:1656` 開始）
- `OWNER_APPROVAL_RE`（`:1750-1752`）和 help 文字（`:1888`）改由 `slug` 與 `retain_release_issues` 產生。

**PR-5（dispatcher）**
- `llmtool/task_submission_cli.py:50-51` 改為 `load_trust().canonical_root`。
- `:1764-1898` 的 `_trusted_d6_helper_source`：`:1804` 的 root 比較，以及 `:1805-1807` 的 origin tuple，都改為 trust 值。
- helper 的呼叫（`:1883-1887`）追加 `--trust-sha256`。
- 錯誤碼沿用現行對應：`ValueError` 對應 `ENV_BOOTSTRAP_SOURCE_UNTRUSTED`；`TrustError` 則直接把 code 往上傳。

**PR-6（review）**
- `task_review_collector.py:202`：origin 改成「必須屬於 `allowed_origins`」，SSH 和 HTTPS 都要精確比對。
  - **整合者註：** 依 SF-2，審查端和 D6 lifecycle 採用同一份 allowlist，不再只允許 SSH。如果 SF-2 先合併，本 PR 只需要把它的常數模組換成 trust 值。
- `task_review_collector.py:221,391`：改用 slug。
- `review_authority.py:31`：URL 改用 slug 和 `reviewer_host_policy_issue`。
- `review_authority.py:104`：origin 規則同上，必須屬於 `allowed_origins`。
- `review_authority.py:291`：改用 slug。
- `review_bundle.py:128`：actions run 的 URL 改用 slug。
- `review_exchange.py:21-22,166`：`AUTHOR_ROOT`、`REVIEW_ROOT` 和 repository 比較也是寫死的錨點，但原本的清單沒有列入。
  - **OPINION**：可以改成 `workspace_root + '/codex'` 和 `canonical_root`。
  - 同一檔案裡的 session UUID（`AUTHOR`、`REVIEWER`）不屬於 #369 的範圍（見 Q6）。

**PR-7（deny-lists）**
- `review_bundle.py:24-26` 的 `PROTECTED` 改為 `(canonical_root, workspace_root)`，維持原本兩項的語意。
- `test_triage_worker.py:173-176` 的 `known` 原本是 dispatcher root，加上 workspace 下的 `codex` 和 `claude`，改為 `(canonical_root, workspace_root)`。
  - **行為會變得更嚴格**：workspace 底下的其他子目錄也會被拒（OPINION：可以接受，見 Q9）。
- `skills/safe-git/preflight.py:143-147` 的 `protected` 改為讀 trust（內嵌 reader 區塊）。trust 缺失時 fail closed，回傳 `TRUST_MISSING`。

**PR-8（CI）**
- `task_completion.py:47-61` 的以下常數改由 `ci_trust` 產生：`_MAC_CHECK`、`_LINUX_CHECK`、`_DOCS_CHECK`、`_REQUIRED_CHECKS`、`_NOT_RUN_CHECKS`、`_CI_RUNNER_NAMES`、`_CI_WORKFLOW_*`、`_LINUX_CI_*`、`_GITHUB_ACTIONS_APP_ID`。
- `:1620`、`:1732`、`:1760`、`:1858` 改用 slug。
- `_collect_ci_evidence`（`:1802-1831`）改為逐一處理 `workflows`：
  - **code 路徑**：每個 `code_check` 非 null 的 workflow 都必須成功；required checks 是所有的 `code_check`；primary 是第一個帶有 `code_check` 的 workflow。
  - **docs-only 路徑**：每個 `docs_check` 非 null 的 workflow 都必須成功；primary 是第一個帶有 `docs_check` 的 workflow。
  - 以 Owner 的現值計算，結果和現行行為逐欄一致：primary 是 Mac workflow，docs 走 Linux workflow 的 `check (docs)`，輸出 dict 的 key 集合不變。
- `_collect_one_ci_workflow`（`:1702-1799`）的 runner 判斷：
  - **`self-hosted`**：沿用 `job.runner_name in runner_names`（`:1762`）；如果有設定 `runner_labels`，另外要求 `set(job.labels) == set(runner_labels)`。
  - **`github-hosted`**：不看 `runner_name`，改為要求 `set(job.labels) == set(runner_labels)`、labels 中沒有 `"self-hosted"`，且 `job.runner_group_name == "GitHub Actions"`。
    - **OPINION**：這組欄位必須先用實際的 API 回應 fixture 驗證，見 Q1。
  - app 的判斷保留不變（`:1789-1791`：id 為 15368、slug 為 `github-actions`、name 為 `GitHub Actions`）。
- `_docs_only_ci`（從 `:1429` 開始）**不變**。它和 workflow 中 classify step 的對齊屬於 repo 內部規則，不是信任錨點。

**PR-9（repository 設定）**
- `config.py:32-36` 的 `REPOSITORY_DEFAULTS` 和 `repository_config()`（`:149-153`）改為從 trust 讀取 slug、`worker_dispatch_issue`、`worker_dispatch_evidence_pr`。
- `worker_dispatch.py:70-78` 透過 `repository_config()` 自動跟著改變。
- `chat_tui_status.py:16` 的 `PR_URL` 改用 slug。
- 如果 `config.yaml` 仍有 `repository` key，一律拒絕並回傳 `TRUST_LEGACY_REPOSITORY_CONFIG`，提示執行 `trust init --import-config`。**OPINION**：不應該兩邊都讀，見 Q4。
- `config.py:94-100` 的 `_read_yaml` 會跟隨 symlink，也沒有 owner 和權限檢查（FACT）。PR-9 之後 `config.yaml` 不再承載信任錨點，所以**不**在這個系列裡強化，另開 issue 處理（OPINION，見 Q10）。

**PR-10（清理）**
- 刪除 `verify-legacy` 子命令和所有遺留常數。
- 新增 guard 測試：掃描 production 的 `.py` 檔（排除 `tests/`），不得出現以下字面值：`/Users/`、`/home/`、`<OWNER_REPO>` slug、runner 名稱樣式、`issues/<數字>`。

---

## 6. 必須保持不變的檢查（FACT 位置）

| 位置 | 檢查 |
|---|---|
| `d6_worktree_lifecycle.py:244-251` | dispatcher checkout：`__file__` 相等、realpath、toplevel、origin，且 `status --porcelain` 必須乾淨（只替換比較值） |
| `d6_worktree_lifecycle.py:252-262` | pinned HEAD 與 helper／lifecycle 原始碼 blob 的相等比對 |
| `d6_worktree_lifecycle.py:95-103` | `_safe_dir`：registry 目錄必須是 0700、屬於 owner、不可為 symlink |
| `d6_worktree_lifecycle.py:421-427` | `show-toplevel` 檢查；common dir 必須以 `.git` 結尾且為 physical path |
| `d6_worktree_lifecycle.py:419,579,712,1366,1535,1659,1778` | `LLMTOOL_D6_TESTING` 的 bypass 語意：只放寬 workspace 相等檢查，不放寬其他檢查 |
| `d6_worktree_lifecycle.py:621,749` | 狀態為 `RETAIN_BLOCKED` 或 `REMOVED` 時拒絕修改 |
| `d6_worktree_env.py:399-448` | registry 欄位、identity、mode、`branch`／`head`、merge-base、`env2_approved_tooling_sha` |
| `task_submission_cli.py:1802-1825` | dispatcher 必須乾淨，且來源與 pinned HEAD 相同 |
| `task_submission_cli.py:1826-1867` | `env2_approved_tooling_sha` 綁定與 blob hash 重算 |
| `task_completion.py:635-665` | `read_d6_registration` 的 no-follow 讀取、0600、nlink、大小與變更偵測 |
| `task_completion.py:1702-1799` | workflow 為 active、`run.event` 為 `pull_request`、`head_sha` 相符、run／job／check 一致、app id 為 15368 |
| `task_completion.py:1850-1860` | PR 的 head、base 為 `main`、merged 狀態 |
| `task_completion.py:1429-` | `_docs_only_ci` |
| `task_review_collector.py:190-200,203-220` | lock 檔、worktree 已 locked、branch 唯一、source drift |
| `review_authority.py:104-107` | `branch_prefix` 不可為 `main` 或 `automation/ai-loop`，以及 `venv_prefix` 規則 |
| `review_bundle.py:27-29,53` | `SECRET_PARTS`；blocked 清單包含 `root`、`root.parent`、`~/.config` |
| `test_triage_worker.py:177-194` | 祖先的 owner、權限與 `.git` 掃描迴圈 |
| `test_triage_worker.py:197-244` | `load_phase0_grant` 的 no-follow 讀取、0600、pin 機制 |
| `skills/safe-git/preflight.py:42-60,141-155` | `directory()` 的 symlink、權限與 macOS ACL 檢查；`private_write` 使用 `O_EXCL` |
| `remote_test.py:222-246` | remote-test 設定的讀取方式 |
| `config.py:137-146` | slug regex（移入 trust 驗證後規則不變） |

---

## 7. TOCTOU、凍結與 rotation

### 7.1 每個 process 只凍結一次
- `load_trust()` 第一次成功後，把結果存在 module 層級，之後同一個 process 不再重新讀檔。chat TUI、PM runtime 這類長時間執行的 process，要讓 trust 的變更生效必須重新啟動（OPINION）。
- 隔離的子程序（第 4 節）自己讀取後，和 `--trust-sha256` 比對，不符時回傳 `TRUST_CHANGED_DURING_RUN`。
- D6 的修改操作在取得 `.lifecycle.lock` **之後**再讀一次 trust，並和凍結值比對 sha，不符時回傳 `TRUST_CHANGED_DURING_RUN`。這樣可以偵測「讀取到上鎖之間」發生的修改（OPINION）。

### 7.2 Registry 戳記
- 新的 record 會加上兩個欄位：
  - `trust_sha256`：完整檔案的 sha256，只用於稽核。
  - `trust_binding_sha256`：對 canonical JSON `{canonical_root, workspace_root, tasks_root, slug, allowed_origins}`（`sort_keys`、ASCII）計算的 sha256，用於強制檢查。
- **OPINION**：分成兩個 digest，是為了讓 CI runner 或 approval issue 更換時，不會連帶讓 D6 worktree 卡住。
- **FACT**：新增欄位和現有 reader 相容。`d6_worktree_env.py:399-417` 用 `record.get` 讀取，`task_completion.py:635-` 也沒有檢查 `set(record)` 是否完全相等。PR-4 仍要用測試確認 `review_authority` 的 registration 解析能接受這兩個新欄位。

### 7.3 Rotation 行為
- 只要 `trust_binding_sha256` 不同，`record_result`、`bind_native_session`、`resume`、`rebind` 都會被拒絕，回傳 `D6_TRUST_ROTATED`。
- Retire 的 dry-run 會在 `reasons` 裡加入 `D6_TRUST_ROTATED`。`--execute-approved-retire` 不會移除這個 worktree，而是把 state 設為 `RETAIN_BLOCKED`、在 `evidence_refs` 加註 rotation，並保留目錄。
- 解除方式：
  - worktree 已經不存在：沿用既有的 `--release-retain TASK_ID=<URL>`，URL 必須來自新 trust 的 `retain_release_issues`。
  - worktree 仍然存在：使用新增的 `--restamp-trust TASK_ID=<URL>`（OPINION，見 Q2）。執行前先驗證 `root_realpath` 位於新的 `tasks_root` 之下、`git_common_dir` 等於新的 `canonical_root/.git`，通過後才重寫兩個戳記。
- 如果只有 `trust_sha256` 不同、binding 相同，照常放行；completion evidence 會記錄當次使用的 `trust_sha256`。

### 7.4 遺留 record（沒有戳記）
- 用目前 trust 的路徑規則判定：`root_realpath` 必須在 `tasks_root` 之下，且 `git_common_dir == canonical_root/.git`。這和現在用常數判定的方式相同。
- **不**自動補上戳記，但 retire 和 reconcile 的輸出會加註 `legacy_unstamped: true`（OPINION）。

---

## 8. 遷移：CLI 行為

### `llmtool trust init`
- 檔案已經存在時拒絕，回傳 `TRUST_EXISTS`。
- 從目前所在的 checkout 推導以下欄位：
  - `canonical_root`：toplevel，且必須是主 worktree。
  - `allowed_origins`：讀取 `origin`。若它是兩種合法形式之一，就**同時**寫入 SSH 和 HTTPS 兩種形式，與現行常數一致。
  - `slug`：由 origin 推得。
  - `workspace_root`：預設為 `<OWNER_HOME>/.local/share/llmtool-dev-workspaces/<repo-name>`。
  - `tasks_root`：預設為 `<workspace_root>/tasks`。**FACT**：與現行 `TASKS_ROOT` 的結構相同（`d6_worktree_lifecycle.py:27-30`）。
- 無法推導的欄位（`approval_refs`、`ci_trust`）有兩種取得方式：
  - 互動模式：必須在 TTY 中執行，逐一詢問並驗證。
  - `--input <draft.json>`：讀取部分 JSON，和推導值合併；同一欄位兩邊的值不同時，回傳 `TRUST_INIT_INPUT_INVALID`。
- `--import-config` 會把 `config.yaml` 的 `repository.dispatch_issue` 和 `repository.dispatch_evidence_pr` 帶入新欄位。
- 寫入流程：
  1. 以 0700 建立 `<OWNER_HOME>/.config/llmtool`；若目錄已經存在，就驗證規則 B。
  2. 用 `O_CREAT|O_EXCL|O_NOFOLLOW` 和 0600 寫入暫存檔。
  3. 執行 `fsync`，再用 `os.link` 或 `rename` 做不覆蓋的安裝。
  4. 再完整執行一次 `load_trust()` 和 `verify_repository()`。
  5. 輸出 `trust_sha256`。
- 若 `tasks_root` 或 `workspace_root` 不存在，以 0700 建立。
- `--replace --confirm-rotation <舊 sha256>`：舊 sha 必須相符。輸出會受影響的 ACTIVE record 數量（binding 不同的那些），只輸出數量，不列出路徑。

### `llmtool trust show`
- 輸出 sanitized JSON，包含：`schema_version`、`trust_sha256`、`trust_binding_sha256`、`ci_trust.mode`、各 workflow 的 path／name／check 名稱，以及 runner 數量。
- 路徑只顯示「OK」或錯誤碼；加上 `--reveal-paths` 才顯示完整路徑，因為路徑屬於 Owner 的本機資訊（OPINION）。
- 失敗時只輸出 `STOP: trust rejected (<CODE>)`，並以 exit 1 結束。

### `llmtool trust verify-legacy`（只存在於 PR-2 到 PR-9 期間）
- 逐欄比對 trust 值和 base 中的遺留常數，只輸出 `field: MATCH|MISMATCH`，**不印出值**。
- 比對的欄位：`tasks_root`、`canonical_root`、`allowed_origins`、`slug`、各 approval 編號、各 workflow 的 path／name／check 與 runner 集合，以及 `not_run_checks`。
- `workspace_root` 的比對方式：`tasks_root.parent` 必須等於 `workspace_root`，且 `review_bundle.PROTECTED[1]` 必須等於 `workspace_root`。

---

## 9. Sanitized 錯誤碼

錯誤訊息只能包含錯誤碼，**不得**包含路徑、值、key 名稱或 OS 錯誤字串。

| 錯誤碼 | 觸發條件 |
|---|---|
| `TRUST_UNSUPPORTED_PLATFORM` | 非 POSIX，或在 Windows 上執行 |
| `TRUST_IDENTITY_INVALID` | `uid != euid`，或 `euid == 0` |
| `TRUST_HOME_INVALID` | `getpwuid` 失敗，或 `pw_dir` 不是 physical 絕對路徑 |
| `TRUST_MISSING` | trust.json 或它的上層目錄不存在 |
| `TRUST_ANCESTOR_INVALID` | 祖先是 symlink 或非目錄、owner 不符、可被 group 或 other 寫入 |
| `TRUST_DIRECTORY_NOT_PRIVATE` | `.config/llmtool` 的權限不是 0700，或 owner 不符 |
| `TRUST_FILE_NOT_PRIVATE` | 不是 regular file、是 symlink 或 FIFO、權限不是 0600、owner 不符、`nlink != 1` |
| `TRUST_TOO_LARGE` | 大小為 0，或超過 16 KiB |
| `TRUST_CHANGED_DURING_READ` | 讀檔過程中 metadata 有變動 |
| `TRUST_MALFORMED` | 不是 UTF-8、有 BOM、重複 key、NaN／Infinity、JSON 語法錯誤 |
| `TRUST_SCHEMA_VERSION_UNSUPPORTED` | `schema_version` 不是 1 |
| `TRUST_SCHEMA_INVALID` | 缺少或多出 key、型別錯誤、超出範圍 |
| `TRUST_PATH_INVALID` | root 不存在、不是 physical path、owner 或權限不符 |
| `TRUST_PATHS_OVERLAP` | 違反 3.1 的第 2–4 項 |
| `TRUST_INSIDE_WORKTREE` | trust 的祖先中有 `.git` |
| `TRUST_REPOSITORY_INVALID` | `verify_repository` 失敗 |
| `TRUST_CI_INVALID` | `ci_trust` 的欄位之間互相矛盾 |
| `TRUST_TOOL_INVALID` | `tools` 中的路徑不安全 |
| `TRUST_CHANGED_DURING_RUN` | 凍結值與重讀值，或與子程序收到的 sha 不符 |
| `TRUST_LEGACY_REPOSITORY_CONFIG` | `config.yaml` 仍有 `repository` key |
| `TRUST_EXISTS` | init 時檔案已經存在 |
| `TRUST_INIT_INPUT_INVALID` | init 的輸入或推導失敗，或兩邊的值衝突 |
| `TRUST_ROTATION_UNCONFIRMED` | `--replace` 時舊 sha 不符或未提供 |
| `D6_TRUST_ROTATED` | record 的 binding 與目前的 trust 不符 |
| `D6_TRUST_RESTAMP_INVALID` | restamp 的前置驗證失敗 |

---

## 10. 測試矩陣

所有測試都放在 `tools/llmtool/tests/`，只使用暫存的 home，不讀取真實的 home。以 root 執行時（例如 Linux CI 容器），用內部參數 `_allow_root=True`。

### PR-1（loader）
- **正向**
  - 合法的最小 `self-hosted` 設定和 `github-hosted` 設定各一份，都能通過驗證。
  - 回傳的物件是 frozen，修改時會拋出例外。
  - `sha256` 和 `binding_sha256` 是穩定的值。
  - 第二次呼叫回傳同一個物件，而且不再重新讀檔（用 mock `os.open` 計算呼叫次數）。
- **檔案系統負向**
  - 檔案缺失、`llmtool` 目錄缺失。
  - 檔案權限為 0644、0400、0700。
  - 目錄權限為 0755、0750。
  - owner 不符（能用 `os.chown` 模擬就用，否則 mock `fstat`）。
  - `nlink=2`；檔案是 symlink；`.config` 是 symlink；home 的某個路徑元件是 symlink。
  - 檔案是 FIFO 或目錄。
  - 祖先權限為 0777；屬於 root 且帶 sticky bit 的祖先應該通過。
  - 祖先含 `.git` 目錄；祖先含 `.git` 檔案（linked worktree 的情況）。
  - 檔案大小為 0、16385、剛好 16384。
  - 讀檔過程中檔案被替換（mock 第二次 `fstat`）。
  - `pw_dir` 不是 physical path；`getpwuid` 拋出 `KeyError`；`uid != euid`；`euid == 0`。
  - `os.name='nt'`。
- **解析負向**：BOM、Latin-1 位元組、重複 key（含巢狀層）、NaN、Infinity、頂層是 array、深度巢狀觸發 `RecursionError`。
- **Schema 負向**（每個欄位都要測）
  - 缺少 key、多出 key、用 bool 當 int。
  - `schema_version` 為 2。
  - slug 為 `a/..`；slug 不合法。
  - origin 和 slug 不一致；origin 使用 `ssh://`；origin 重複；origin 數量為 3。
  - issue 編號為 0 或 `10**9+1`。
  - `retain_release_issues` 為空或有重複。
  - `mode` 不合法；app id 為 15367。
  - `self-hosted` 模式下 `runner_names` 為 null；`github-hosted` 模式下 `runner_names` 不是 null。
  - runner 名稱含 `*` 或 `?`；`runner_labels` 含 `self-hosted`。
  - workflow path 為 `../x.yml`。
  - check 名稱重複；沒有任何 `docs_check`。
  - `tools.git` 是相對路徑、是 group 可寫的檔案、是 symlink。
- **Invariant 負向**
  - `canonical_root` 和 `workspace_root` 相等，或互為祖先／子孫。
  - `tasks_root` 不在 `workspace_root` 之下。
  - trust 目錄位於 `canonical_root` 之內。
  - 某個 root 等於 home。
  - 某個 root 不存在，或不是 physical path（含 `..` 或是 symlink）。
- **錯誤訊息 sanitization**：每個負向案例都斷言 `str(exc) == code`，而且輸出不含暫存路徑。
- **`verify_repository`**：用暫存的 git repo 測試，涵蓋正向案例，以及以下應拒絕的情況：
  - linked worktree 被當成 canonical root；
  - origin 不在 `allowed_origins` 中；
  - `show-toplevel` 不符。

### PR-2（CLI）
- init 推導出的值正確：用 HTTPS origin 的暫存 repo 測試，要同時產生兩種 origin 形式。
- 檔案已存在時回傳 `TRUST_EXISTS`。
- 不在 TTY 中、又沒有提供 `--input` 時，回傳 `TRUST_INIT_INPUT_INVALID`。
- `--input` 和推導值衝突時拒絕。
- 產生的檔案權限為 0600、目錄為 0700，且 `nlink` 為 1。
- 寫入中途失敗時，不會留下寫到一半的 trust.json。
- `--replace`：沒有提供舊 sha 或舊 sha 錯誤時拒絕；正確時成功，並輸出受影響的數量。
- `show` 預設不印出路徑。
- `verify-legacy`：全部相符時輸出全部 MATCH；任一欄位不同時，對應欄位輸出 MISMATCH，而且輸出不含值。
- `--import-config` 能把 `config.yaml` 的值帶入新欄位。

### PR-3（lifecycle 與 env helper）
- 三份內嵌的 reader 區塊，都和 `trust.py` 中的區塊逐位元組相同。
- trust 缺失時以 exit 1 結束，stderr 精確等於 `STOP: D6 lifecycle rejected (TRUST_MISSING)`，不含 traceback 和路徑。
- `--repo-root` 未指定時解析為 trust 值；指向不存在的路徑時回傳 sanitized code，不出現 traceback。
- `workspace_root` 不符時：未設定 `LLMTOOL_D6_TESTING` 就拒絕，有設定就放行。現有約 30 個測試沿用，改為注入 trust。
- `LLMTOOL_D6_TESTING` **不會**影響 trust 的讀取位置或結果；設定它之後，trust 缺失時仍回傳 `TRUST_MISSING`。
- origin 為 trust 中的 HTTPS 形式時接受；不在清單中時回傳 `D6_ORIGIN_INVALID`。
- 子程序收到錯誤的 `--trust-sha256` 時回傳 `TRUST_CHANGED_DURING_RUN`。
- env helper 的 origin 不符時回傳 `D6_SOURCE_BINDING_MISMATCH`。
- 第 6 節列出的 lifecycle 與 env 檢查，原有測試全部通過。

### PR-4（戳記與 rotation）
- create 之後，record 帶有兩個戳記。
- binding 相同、只有 `trust_sha256` 不同時，`record_result` 放行。
- binding 不同時：`record_result`、`bind`、`rebind` 回傳 `D6_TRUST_ROTATED`；retire 執行後狀態變成 `RETAIN_BLOCKED`，目錄仍然存在。
- 遺留 record：路徑相符時放行，不符時拒絕。
- `--release-retain` 的 URL 來自新 issue 時接受；來自已不在 trust 中的舊 issue 時拒絕。
- `--restamp-trust` 的正向與負向案例。
- 取得 lock 之後 trust 被修改時，回傳 `TRUST_CHANGED_DURING_RUN`。
- `task_completion.read_d6_registration`、`d6_worktree_env` 和 `review_authority` 都能讀取帶戳記的 record。

### PR-5（dispatcher）
- `_trusted_d6_helper_source`：trust 的 root 等於 `__file__` 所在的 root 時通過；不相等時回傳 `ENV_BOOTSTRAP_SOURCE_UNTRUSTED`。
- trust 缺失時直接回傳 `TRUST_MISSING`，不執行任何 git 指令。
- helper 的 argv 包含 `--trust-sha256`。
- 既有的 dispatcher 乾淨度測試與 approved-blob 測試全部通過。

### PR-6（review）
- 換成別的 slug 時，collector、authority 和 bundle 的 URL 檢查都跟著改變。
- SSH 和 HTTPS 形式的 origin（屬於 `allowed_origins`）都接受。以下不在清單內的形式要拒絕：`ssh://`、結尾多一個 `/`、缺少 `.git`、大小寫不同。
- 把 issue #120 換成其他編號時，舊 URL 被拒、新 URL 被接受。
- 若 `review_exchange` 納入範圍，它的 root 改用 trust 值。

### PR-7（deny-lists）
- grant 位於 `canonical_root` 或 `workspace_root` 底下（包含 `tasks/`）時被拒；位於 trust 目錄以外的私有目錄時通過。
- bundle 的寫入位置落在 `PROTECTED` 底下時被拒。
- preflight 的 `private_write` 落在受保護路徑時被拒；trust 缺失時 preflight fail closed。

### PR-8（CI）
- 使用錄製下來的 API fixture。
- **self-hosted**
  - 用 Owner 的現值計算時，輸出 dict 和重構前逐欄相同。這是 golden 測試，code 和 docs-only 兩條路徑都要測。
  - runner 名稱不在清單中時回傳 `CI_WORKFLOW_JOBS_INVALID`；runner 名稱只是前綴或子字串相似（類似萬用字元的意圖）時也要拒絕。
  - 設定了 `runner_labels`、但 labels 不同時拒絕。
- **github-hosted**
  - labels 完全相符、`runner_group` 為 GitHub Actions 時成功。
  - labels 含 `self-hosted` 時拒絕；`runner_group_name` 不同時拒絕；labels 是子集或超集時拒絕。
  - check 的 app id 不是 15368 時拒絕；workflow path 不同時回傳 `CI_WORKFLOW_MISMATCH`。
- **共通**
  - `run.head_sha` 不符時回傳 `CI_WORKFLOW_RUN_MISMATCH`。
  - `full_name` 是其他 slug 時回傳 `PR_HEAD_MISMATCH`。
  - 有三個以上 workflow 時，required checks 是所有 `code_check` 的聯集。

### PR-9（repository 設定）
- `worker_dispatch` 的 pattern 改由 trust 產生。
- `config.yaml` 含 `repository` key 時回傳 `TRUST_LEGACY_REPOSITORY_CONFIG`。
- `chat_tui_status` 的 URL 使用 slug。
- `doctor` 等不需要 trust 的命令，在 trust 缺失時不會失敗（OPINION：只有 D6、review、CI 和派工路徑需要 trust）。

### PR-10（清理）
- guard 掃描測試。
- 確認 `verify-legacy` 已移除。
- 完整測試套件在 Mac CI 和 Linux CI 上都通過。

---

## 11. 安全風險分析（除另有標註，皆為 OPINION）

| 風險 | 緩解措施 |
|---|---|
| Developer worktree 可寫，能竄改 trust.json | trust 位於 home 下的 config 目錄，任何祖先都不得含 `.git`，且與三個 root 互斥；檔案必須是 0600、目錄必須是 0700。**殘留風險**：D6 Developer 在 sandbox 外與 Owner 是同一個 uid，同 uid 的程式本來就能修改 Owner 的檔案。trust 無法防禦同 uid 的惡意程式，只能防止設定錯誤與意外 |
| 用 symlink 或 hardlink 置換檔案 | 以 `O_NOFOLLOW` 做 dirfd 走訪、要求 `nlink==1`，並做三重 metadata 比對 |
| 透過 `$HOME` 或環境變數改變讀取位置 | 只使用 `getpwuid`，不提供任何環境變數覆寫 |
| 讀取之後才被修改（TOCTOU） | 每個 process 凍結一次；子程序比對 sha；上鎖後再讀一次；registry 記錄戳記 |
| 靜默退回寫死的值 | PR-10 刪除常數並加上 guard 測試。PR-3 到 PR-9 期間，trust 缺失一律 fail closed；`verify-legacy` 只負責比對，不會被當成預設值 |
| `github-hosted` 模式下，PR 可以修改 workflow 的內容（head SHA 上的 workflow 由 PR 作者控制） | 現行的 `self-hosted` 模式同樣有這個問題（FACT：只比對 `run.path`）。建議另開 issue，要求 workflow 的 blob 必須等於 base 版本，除非 Owner 核准（見 Q11） |
| 用 `runner_labels` 冒充 GitHub-hosted runner（self-hosted runner 可以設自訂 label） | 要求 labels 中沒有 `self-hosted`，且 `runner_group_name == "GitHub Actions"`。GitHub 的實際語意要用真實 fixture 驗證（見 Q1） |
| 錯誤訊息洩漏路徑 | 只輸出錯誤碼；`trust show` 預設遮蔽路徑；測試斷言輸出不含路徑 |
| 內嵌的 reader 副本彼此漂移 | 用逐位元組相等的測試強制一致 |
| macOS ACL 讓其他使用者取得寫入權 | PR-1 不檢查，屬於殘留風險（見 Q8） |
| 以 root 執行 | 預設拒絕（見 Q7） |
| rotation 讓 worktree 變成孤兒或被誤刪 | 改成 `RETAIN_BLOCKED` 並保留目錄，需要 Owner 核准才能解除 |

---

## 12. 待 Owner 決定的問題

- **Q1**：`github-hosted` 模式的 runner 判斷，是否採用「labels 完全相符、不含 `self-hosted`、且 `runner_group_name == "GitHub Actions"`」？這需要 Owner 提供一份實際的 job API 回應當作 fixture（不可含 secret）。larger runner 是否也要支援？
- **Q2**：rotation 之後，對仍然存在的 worktree，是否新增 `--restamp-trust`（需要核准 URL）？還是只能 retire 後重建？
- **Q3**：隔離腳本的 reader 採用「內嵌逐位元組相同的區塊」，是否接受？
- **Q4**：`config.yaml` 中的 `repository` key，要直接拒絕（建議），還是在過渡期內只要和 trust 相同就允許？
- **Q5**：`create_worktree` 是否新增 `canonical_root == trust.canonical_root` 的強制檢查？這會讓行為變得更嚴格。
- **Q6**：`review_exchange.py:21-22` 的 `AUTHOR_ROOT`、`REVIEW_ROOT` 是否納入 #369 的範圍？
- **Q7**：`euid == 0` 是否一律拒絕？
- **Q8**：macOS 是否加入 ACL 檢查（比照 preflight 的 `ls -lde`）？
- **Q9**：`test_triage_worker` 的 deny-list 從 `{codex, claude}` 擴大為整個 `workspace_root`，這個更嚴格的行為是否接受？
- **Q10**：`config.yaml` 的 `_read_yaml` 強化另開 issue 處理，是否同意？
- **Q11**：是否要求 CI workflow 檔在 head SHA 上的 blob 必須等於 base（main）上的 blob？可以另開 issue。
- **Q12**：`ci_trust` 的模式是整份檔案二選一（本規格的設計），還是允許每個 workflow 各自指定？
- **Q13**：只用於顯示的 `not_run_checks`，要移入 trust，還是保留為 repo 常數？

---

## 13. 完成條件（每個 PR 都適用）
- 第 10 節列出的測試全部通過；第 6 節列出的既有檢查，對應的測試沒有被移除或放寬。
- 報告中載明受審的 SHA，並由另一個模型家族的 Reviewer 給出 PASS（依 CLAUDE.md）。
- 不在 repo 中加入 Owner 的路徑、使用者名稱、IP 或 runner 名稱。
- 從 PR-3 開始，PR 描述要附上 Owner 在 Mac 上執行 `trust verify-legacy` 全部 MATCH 的證據。沒有實機證據，不得宣稱 Mac 可用。

---
_Generated by [Claude Code](https://claude.ai/code)_
