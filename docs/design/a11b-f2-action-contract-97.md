> **DESIGN ONLY / NOT AUTHORIZED**
>
> 本則是 READ-ONLY 調查產出的 F2 合約設計稿，不是實作，也不授權任何動作。F2.0 不授權任何寫入、Commit、Push 或 PR；F2.1 及之後每一階段都需要獨立 Security review 與 Owner 另行授權。
>
> **來源討論：** [A11b F2 設計來源（issue #97 comment 6552968268）](https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6552968268)。
>
> **整合者註：與其他規格的對齊**
> 1. **(updated) `trust.json` loader 已在目前 main 實作**，可載入並驗證 owner-only 設定，產生 repository binding；規格來源仍是 [#369 comment 6552692881](https://github.com/davidcatcat/davidcattoolsproject/issues/369#issuecomment-6552692881)，實作見 [`trust.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/trust.py#L413-L464)。F2 的 `repo_identity` 可沿用 `canonical_root`、`allowed_origins`、`slug` 與 binding；F2 如何在每個入口強制重驗仍屬設計問題。
> 2. **§3.2 和 §6 引用的 `approve(...)`**，就是本 issue 上一則 A11b.3 設計中的 approval record 與 ledger（https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6552954731 ）。兩者的對應方式：
>    - F2 的五個 `action_class` 都對應 A11b.3 的 `REPO_MUTATION` 類別，各自另有子類別；
>    - issuer 只限 `OWNER_LOCAL_TTY`，初期不開放 GitHub 留言或遠端（`REMOTE_OWNER`）核准。
> 3. **§9.2 的開放問題**保持開放；本文件未記錄 Owner 答覆。
>
> 實作要分階段進行，每個階段都要通過獨立的 Security review，並取得 Owner 另外的授權。

# A11b F2：llmtool 直接修改使用者 Repository 檔案與 Git 的 Action／Approval 合約（設計稿）

- **狀態：DESIGN ONLY / NOT AUTHORIZED。** F2.0 不授權任何寫入、Commit、Push 或 PR。F2.1 及之後每一階段各自需要獨立 Security review 與 Owner grant。
- **Base commit：** `5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45`（目前 `origin/main`）。F1 的歸檔參考點是 `d41600ff98568770fb918fdf4a3309dd144a0dfe`。
- **標記：** **FACT** = 已用 repo 原始碼或歸檔文件驗證；**OPINION** = 設計建議；**UNKNOWN** = 尚未驗證，或需要 Owner／其他方決定。

---

## 1. F1 做了什麼（FACT），以及為什麼它不能當作 F2 的授權

### 1.1 F1 的事實（來自歸檔與 `d41600ff`）

- **範圍與驗收：** A11b.2.2.2 F1 是「只在私有、可丟棄的 fixture 中執行 Git ADD」。PR #84 合併到 `main@46b17b13`，記錄了獨立 Security 與 Functional QA、exact-head CI 及 Mac 原生驗收（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **(updated) 只有一個 Git 動作：** F1 只實作了 `GitOperation.ADD`；目前 main 的 Action contract 也只列出 `ADD`（[`action_contract.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/action_contract.py#L187-L189)）。
- **只吃資料：** F1 的 `_git_add_private.run_fixture_add(baseline, changes)` 入口會建立私有 fixture，只接受資料，不接受 repo 路徑、指令或 callback（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **拒絕 linked worktree：** F1 的 metadata 檢查遇到 `commondir`、`objects/info/alternates`、`info/sparse-checkout` 或 `worktrees` 就拒絕，因此不支援 linked、sparse 或 alternate repo（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **固定的安全 Git 設定：** F1 的 Git 子程序一律套用 `SAFE_CONFIG`，並設定 `GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_GLOBAL=/dev/null`、`GIT_ATTR_NOSYSTEM=1`（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。`SAFE_CONFIG` 包括：
  - `core.hooksPath=/dev/null`、`core.fsmonitor=false`
  - `credential.helper=`、`core.sshCommand=`
  - `protocol.*.allow=never`
  - `gpg.program=`、`commit.gpgSign=false`
  - `gc.auto=0`、`maintenance.auto=false`
- **路徑檢查：** F1 用 `O_NOFOLLOW` 逐一開啟路徑元件，要求擁有者是目前的 euid、不可被 group 或 other 寫入、regular file 的 `st_nlink == 1`，並拒絕以 root 執行（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **只支援 Darwin：** F1 程序身分檢查（`_git_process_identity.py`）只能在 Darwin 上用；其文件說明它不是可抵抗 hostile same-UID reaper 的 atomic handle（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **C3 detached-escape 未驗證：** F1 C3 端到端測試為 **NOT_VERIFIED**，僅在信任同 UID 程式的前提下被接受（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **已歸檔：** 2026-10-20 的歸檔已把 F1 模組從現行程式碼移除（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。

#### 1.1.1 目前 main 的相關實作狀態（FACT）

- **(updated) WP-A 路徑硬化：** Action contract 要求 NFC 正規形式、以 NFC+casefold 比對路徑碰撞，並拒絕 `.gitmodules`、`.gitattributes`、`.lfsconfig` 與 `.github/workflows/**` 路徑；OpenCode scope 檢查也比對 NFC/casefold 碰撞，並以 dirfd + `O_NOFOLLOW` 錨定父目錄、拒絕 symlink leaf（[`action_contract.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/action_contract.py#L69-L109)、[`opencode_worker.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/opencode_worker.py#L330-L405)）。這些檢查只證明所列 schema/scope 邊界；不證明 F2 有安全的原子寫入或完整 TOCTOU 執行邊界。
- **(updated) WP-B ledger recovery：** `dispatch_review_handoff` 在 ledger 目錄鎖內首次 handoff 時呼叫 `ledger.recover()`，把舊 `CONSUMED` receipt 復原成 `EFFECT_UNKNOWN`；它不把未知效果當成功或自動重做（[`review_handoff.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/review_handoff.py#L412-L447)、[`authorization_ledger.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/authorization_ledger.py#L553-L557)）。
- **(updated) 尚無公開的 production Owner issuer：** action CLI 的成功輸出仍是 `authorized=false`；public `review_authority_factory()` 仍回傳 `UnavailableReviewAuthority`。現有 review handoff issuer 不構成 F2 Owner authorization（[`action_contract_cli.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/action_contract_cli.py#L94-L115)、[`review_authority.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/review_authority.py#L604-L607)）。

## 1.2 F1 與 F2 的差異

| 面向 | F1 | F2 需要 |
|---|---|---|
| 目標 | 自己建立、用完即丟的 fixture | 使用者既有的 repo |
| Repo 佈局 | 拒絕 linked worktree | **必須**支援 D6 linked worktree |
| 動作 | 只有 ADD | 寫檔、ADD、COMMIT、PUSH、開 Draft PR |
| 核准者 | 沒有 approval API | 真實、可驗證的核准者，以及一次性的核准 |
| 回復 | 出事時永久保留 fixture | 必須能安全撤銷 commit 和 remote branch |
| 平台 | 程序身分只支援 Darwin | 依 Owner 決定支援 POSIX |

- **FACT：** 歸檔明文寫著「Fixture-only F1 approval cannot be reused as F2 authority」（[merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)）。
- **OPINION：** F1 最值得重用的是技術零件：`SAFE_CONFIG`、no-follow 路徑走訪、blob OID 綁定，以及「UNKNOWN 永遠不能升級為成功」的原則。它的授權不能沿用。

---

## 2. F2 動作清單

### 2.1 範圍內

每一項都是獨立的 `action_class`，各自需要核准。

| action_class | 效果 | 前提 |
|---|---|---|
| `F2_FILE_WRITE` | 在已註冊的 D6 worktree 內，對一組精確的路徑建立、覆寫或刪除 regular file | 每個檔案修改前與修改後的 OID 都已綁定 |
| `F2_GIT_ADD` | `git add -- <精確路徑>` | 已綁定 index 修改前的 digest |
| `F2_GIT_COMMIT` | 在 task branch 上建立一個非 merge 的 commit | 已綁定預期的 tree OID、parent、作者和訊息 |
| `F2_GIT_PUSH_BRANCH` | 以一般方式（非 force）push 到**非 default** 的 task branch | 已綁定 remote URL 和 remote ref 的舊值 |
| `F2_PR_OPEN_DRAFT` | 開一個 **draft** PR | head SHA 必須等於剛 push 的 commit |

### 2.2 明確排除在範圍外（F2 一律拒絕）

- **分支：** push 到 default branch／`main`、受保護分支、`automation/ai-loop`，或別人的 task branch。
- **Force 類操作：** force push、用 `--force-with-lease` 覆寫、帶 `+` 的 refspec、刪除 remote ref。
- **Tag 與 Release：** 任何 tag 操作、GitHub Release。
- **改寫歷史：** `rebase`、`commit --amend`、任何模式的 `reset`、`filter-*`、`replace`、`notes`。
- **CI 定義：** `.github/workflows/**`、`.github/actions/**`。F2.0 到 F2.3 一律拒絕；最早到 F2.4，才可能在 D13 下只由 Owner 核准。
- **特殊 repo 功能：** submodule、Git LFS、sparse checkout、split index、alternates。
- **Git 設定：** 修改任何層級的 Git config、hooks、`info/attributes`、`info/exclude`。
- **分支操作：** merge、cherry-pick、stash、checkout 到其他分支、`git worktree` 操作。
- **canonical checkout：** 對 canonical Integrator checkout 的任何寫入。

---

## 3. 範圍綁定（Action Plan 與 TOCTOU）

### 3.1 ActionPlan 結構（OPINION）

```
ActionPlan {
  plan_version, plan_id (random),
  repo_identity:   {trust_binding_sha256, canonical_root_dev_ino, common_dir_dev_ino,
                    origin_url_normalized, default_branch}
  d6_task:         {task_id, registry_record_sha256, worktree_root, root_dev_ino,
                    branch, writer_agent_id}
  base_sha, expected_head_before,
  index_digest_before,
  paths:           sorted exact list of normalized relative paths (NFC, no '..')
  per_path:        {pre: ABSENT|{oid,mode}, post: ABSENT|{oid,mode}}
  patch_sha256,
  steps:           ordered subset of [FILE_WRITE, ADD, COMMIT, PUSH, PR_DRAFT]
  commit:          {expected_tree_oid, parent, author, committer, message_sha256}
  push:            {remote_name, remote_url_sha256, remote_ref, expected_remote_old}
  pr:              {base_branch, title_sha256, body_sha256, draft: true}
  limits:          {max_files, max_total_bytes, max_diff_lines}
  created_at, expires_at
}
plan_hash = SHA-256(canonical JSON(ActionPlan))
```

### 3.2 核准如何綁定

- 本地步驟（WRITE、ADD、COMMIT）可以用一次核准涵蓋整個計畫。
- **PUSH 和 PR_DRAFT 必須各自另外核准**，因為它們會離開本機，並影響共享的 remote。
- 核准紀錄必須只能使用一次、受 TTL 限制，並綁定 `plan_hash`、`issuer` 和 `action_class`。
- redeem 沿用 ledger 現有的狀態流程：ISSUED → CONSUMED → EFFECT_DONE 或 EFFECT_UNKNOWN（[`authorization_ledger.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/authorization_ledger.py#L1-L9)）。
- EFFECT_UNKNOWN 永遠不算成功，也不能自動重試（[`review_handoff.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/review_handoff.py#L181-L190)、[`authorization_ledger.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/authorization_ledger.py#L553-L557)）。

### 3.3 每一步執行前後都要重新驗證

1. 重新讀取 trust 和 D6 record，比對 hash，並確認目前的 writer 仍是這份計畫的 writer。
2. 用 `O_NOFOLLOW` 走訪 worktree root 的每個元件，比對 dev/ino。
3. 讀取 `.git` 檔，再依序找到 gitdir 和 `commondir`，比對 dev/ino 和 `repo_identity`。這是 F1 沒有涵蓋的新檢查。
4. 比對 `HEAD`、branch symref、index digest，以及每個目標路徑的 lstat 和 OID。
5. 只要有任何一項不符，就回傳 `REFUSED_DRIFT`：不執行、不做部分修補、不重新規劃，並保留現場。
6. 步驟完成後比對修改後的狀態；不符就記為 `EFFECT_UNKNOWN`，並停止後續所有步驟。

- 比對時不使用 `git status` 或 `git diff`，因為它們可能觸發 clean filter。改為直接讀檔計算 blob OID，並自行解析 index。

---

## 4. 防護範圍

### 4.1 威脅模型

- **FACT (updated)：** 現有 ledger 與 authorization policy 明確說明其同 UID 限制（[`authorization_ledger.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/authorization_ledger.py#L11-L21)、[`authorization_policy.py` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/llmtool/authorization_policy.py#L1-L11)）。
- **OPINION：** F2 沿用這個前提，但要在使用者看得到的地方明寫：「同一使用者帳號下的惡意程式可以偽造核准，或在途中修改檔案；F2 不是沙箱。」
- Repository 的**內容**一律不可信，包括 config、attributes、hooks、檔名和 symlink。

### 4.2 路徑與檔案系統

- **只接受**相對路徑，而且必須經過 NFC 正規化，不含 `..`、控制字元或反斜線。
- 在 macOS 這類不分大小寫的檔案系統上，要檢查大小寫碰撞；Unicode 正規化後發生碰撞也要拒絕。
- 每個父目錄都用 `openat(..., O_NOFOLLOW|O_DIRECTORY)` 開啟。寫入時先在同一個目錄建立暫存檔，`fsync` 之後再用 `renameat` 換上。
- **拒絕：** symlink、hardlink、非 regular file、跨 device，以及帶執行權限位元的檔案。
- **拒絕**任何指向 `.git`（包括大小寫變體）、`.gitmodules`、`.gitattributes` 或 `.lfsconfig` 的路徑。

### 4.3 拒絕清單與允許清單（OPINION）

- **拒絕清單：**
  - `.github/workflows/**`、`.github/actions/**`
  - `**/.env*`、`**/*.pem`、`**/*.key`、`**/id_rsa*`、`**/credentials*`、`**/*secret*`
  - `.git*` 設定檔
  - `trust.json`、approval ledger、D6 registry
- **允許清單：** 計畫中列出的精確路徑集合，就是允許清單。
- **內容掃描：** 修改後的內容不得含有疑似憑證的字串。

### 4.4 Git 執行面

- 所有 Git 子程序都沿用 F1 的 `SAFE_CONFIG`，並固定使用 `PATH`、`LC_ALL=C`、stdin=`/dev/null`。
- **本地 config 預檢**，遇到以下設定就拒絕：
  - `include.*`、`includeIf.*`、`core.worktree`
  - `filter.*`、`diff.*.textconv`、`merge.*.driver`
  - `core.sshCommand`、`credential.*`、`url.*.insteadOf`
  - `remote.*.receivepack`、`uploadpack`
  - `gpg.*`、`core.splitIndex`
- **本地步驟完全禁止網路。**
- **COMMIT** 改用 `git commit-tree` 加上 `update-ref <branch> <new> <expected_old>`。update-ref 是原子的 compare-and-swap，而且這樣就不會觸發 hooks、editor 或 gpg。
- **PUSH** 是唯一需要網路的 Git 步驟：
  - 只允許用 `https` 連到 trust 中綁定的 URL；
  - refspec 固定為 `<sha>:refs/heads/<task-branch>`，不加 `+`；
  - 憑證來源目前是 **UNKNOWN**，而且憑證不得被印出或寫入紀錄。
- **Detached HEAD：** HEAD 必須是指向 task branch 的 symref，否則拒絕。

### 4.5 子程序控制

- 每個 Git 子程序都要放在新的 process group 或 session，並設硬 timeout 和輸出上限。結束後要確認沒有殘留的子孫程序。無法確認時記為 `EFFECT_UNKNOWN`，並保留 worktree。
- **FACT：** `write_boundary.for_platform()` 的平台限制見 [merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)；本稿不把該 F1 邊界視為 F2 可用的執行器。
- **OPINION：** F2 本身**不執行任何 repo 內的工具**，包括 test、build 和 formatter。

### 4.6 大小上限（OPINION，初始值待 Security review 調整）

- 每份計畫最多 50 個檔案。
- 修改後的內容總計最多 1 MiB；單一檔案最多 256 KiB。
- diff 最多 5,000 行。
- 拒絕 binary 檔案。

---

## 5. 回復與稽核

### 5.1 執行前快照

- 建立私有 ref `refs/llmtool/f2/<plan_id>/pre-head`。這個 ref 不 push。
- 把 index 的原始 bytes，以及每個被覆寫檔案的修改前內容，存到 repo 外、只有 owner 可讀寫的 recovery 目錄。

### 5.2 撤銷方式

| 已完成的步驟 | 安全的撤銷方式 | 不允許的做法 |
|---|---|---|
| FILE_WRITE | 用修改前的內容原子寫回。前提是目前的 OID 等於修改後的 OID | 直接覆寫 |
| ADD | 用保存的 index bytes 還原 | `git reset` |
| COMMIT（尚未 push） | 用 `update-ref` 做 compare-and-swap，指回原本的 commit | `reset --hard` |
| PUSH | **預設不自動刪除 remote ref**；改為新增一個 revert commit 並一般 push（需要新的核准），或由 Owner 手動刪除分支 | force push、自動刪除 remote ref |
| PR_DRAFT | 關閉 draft PR（需要新的核准） | 刪除 PR 歷史 |

- 任何撤銷本身也是一份新的 ActionPlan，同樣需要核准。

### 5.3 稽核紀錄

- 稽核紀錄是 append-only 的 JSONL 檔，只有 owner 可讀寫，放在 repo 外，並以 hash chain 串接。
- 紀錄中不寫入檔案內容、憑證、home 路徑、使用者名稱或 IP。
- 同一個 UID 仍然可以改寫這份紀錄，所以 hash chain 只能偵測竄改，無法防止竄改。

---

## 6. 核准者的真實性

| action_class | 誰可以核准 | 誰不能核准 |
|---|---|---|
| WRITE、ADD、COMMIT | Owner | 提出計畫的 PM、執行的 Developer、llmtool 本身、模型輸出 |
| PUSH 到非 default branch | 初期只有 Owner | 同上 |
| PR_DRAFT | 初期只有 Owner | 同上 |
| WORKFLOW_CHANGE（D13） | 只有 Owner 本人 | 任何 agent |

- **硬性不變式：**
  - 核准者不能是計畫的作者，也不能是這個 task 的 writer；
  - PM 不能核准自己提出的計畫；
  - Developer 不能核准自己的計畫；
  - Reviewer 的核准不等於 Owner 的產品驗收。
- **FACT：** 目前角色可能共用同一 GitHub login，因此 login 身分不能證明核准者不同；Owner channel 與身份政策記錄於 [merged PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)。核准者真實性仍需本機 out-of-band 互動。

---

## 7. 什麼時候**不需要** F2

- **FACT (updated)：** Roadmap 明定 F2 尚未授權，並將 F2 與 Developer 的既有工作區操作分開（[`ROADMAP.md` at base](https://github.com/davidcatcat/davidcattoolsproject/blob/5c2853ffac38b1e8f4bb2d836ee8ea7bf0e22d45/tools/llmtool/ROADMAP.md#L20-L24)）。Worker 自有 Git 操作不因此成為 F2 授權。
- **不需要 F2 的情況：**
  - Developer 在自己的 D6 worktree 中，用既有權限寫入；
  - Integrator 依 safe-git 流程手動 push 或 merge；
  - 只讀的分析；
  - D6 lifecycle 本身的 create 和 retire；
  - company `gw` 路線（它沒有檔案、Shell 或 Git 權限）。
- **需要 F2 的情況：** 使用者在 `llmtool chat` 中要求「幫我改這個檔案並 commit／push」，由 llmtool 程序本身執行。
- **只能有一個 writer：** F2 執行時，llmtool 就是這個 D6 task 唯一的 writer，必須先透過 D6 registry 取得 writer 身分。F2 絕不寫入 Developer 擁有的 worktree。
- 第一版不支援「Developer 寫檔、F2 負責 push」這種混合模式。

---

## 8. 分階段 PR 與審查檢查點

| 階段 | 內容 | 允許的效果 | 通過條件 |
|---|---|---|---|
| F2.0 | 本合約（文件） | 無 | 獨立的 **Security review**，加上 Owner 閱讀並回答第 9 節的問題 |
| F2.1 | Dry-run planner：產生 ActionPlan、做所有預檢、偵測 drift | 只讀 | 單元測試加對抗測試、獨立 Security review、exact-head CI |
| F2.2 | 在可丟棄的 fixture 中執行（包括 linked worktree fixture） | 只限 fixture | 獨立的 Security 與 Functional QA、Mac 原生加 Linux 驗收、detached-escape 測試 |
| F2.3 | 在真實 repo 的 D6 worktree 中執行本地步驟，前面有 A11b.3 的核准把關 | 只限本地 | A11b.3 已合併、Owner 授權、真實環境的證據 |
| F2.4 | PUSH 到非 default branch、開 Draft PR | 影響共享的 remote | 另一次 Security review、Owner 授權、憑證處理審查 |

- F2.2 之前，不得接上任何對外公開的 CLI 入口。

### 8.1 測試矩陣（含對抗案例）

| 類別 | 案例 | 預期結果 |
|---|---|---|
| 綁定 | 改動計畫中的任一欄位 | REFUSED |
| TOCTOU | 核准之後、執行之前或兩個步驟之間，HEAD、index 或檔案被改動 | REFUSED_DRIFT |
| 核准 | 重放、過期、action_class 不符、scope 不符、同一個核准用兩次 | DENY |
| 核准者 | PM 或 Developer 核准自己的計畫、模型輸出 JSON 宣稱自己是 USER | DENY |
| 路徑 | `../`、絕對路徑、`.git/config`、`.GIT/hooks`、Unicode 正規化碰撞、大小寫碰撞 | REFUSED |
| Symlink 與 hardlink | 目標或父目錄是 symlink、核准後被換成 symlink、`nlink=2` | REFUSED |
| `.git` 逃逸 | `.git` 檔指向其他 gitdir、`commondir` 被改、設定了 `core.worktree` | REFUSED |
| Hooks、filter、config | pre-commit hook、`filter=evil`、`include.path`、`url.insteadOf`、`credential.helper` | 不執行，或 REFUSED |
| 分支與 push | 目標是 main 或別人的 branch、detached HEAD、帶 `+` 的 refspec、tag、remote URL 被改 | REFUSED |
| 拒絕清單與大小 | workflow 檔、`.env`、私鑰樣式的內容、超過上限、binary | REFUSED |
| 程序 | Git 卡住、輸出爆量、子程序 detach 後繼續執行 | timeout，記為 EFFECT_UNKNOWN 並保留現場 |
| 崩潰 | 在 CONSUMED 和 EFFECT_DONE 之間程序被終止 | 復原後記為 EFFECT_UNKNOWN，不重做 |
| 隱私 | 稽核紀錄中出現 home 路徑、使用者名稱或憑證 | 測試失敗 |

---

## 9. 安全風險與 Owner 問題

### 9.1 風險表

| # | 風險 | 嚴重度 | 緩解方式 | 殘餘風險 |
|---|---|---|---|---|
| R1 | 同 UID 的惡意程式偽造核准，或在途中修改檔案 | High | 明示前提、out-of-band 核准、每一步重新驗證 | 無法消除 |
| R2 | 透過 hooks、filter 或 config 執行 repo 內的程式碼 | High | SAFE_CONFIG、config 預檢、改用 commit-tree | Git 未來新增的設定項 |
| R3 | symlink、hardlink 或 `.git` 指向，導致寫到 repo 外 | High | O_NOFOLLOW 走訪、dev/ino 比對、nlink 檢查 | 同 R1 |
| R4 | 誤推到 default branch，或覆寫別人的工作 | High | 分支拒絕清單、compare-and-swap、不用 force | 仍建議在 GitHub 端開啟分支保護 |
| R5 | push 或開 PR 時憑證外洩 | High | 不寫入紀錄、固定 URL | 憑證來源 UNKNOWN |
| R6 | 修改 workflow，濫用 CI 權限 | High | F2.0 到 F2.3 全部拒絕；D13 只有 Owner 能核准 | — |
| R7 | 核准太頻繁，Owner 習慣性按同意 | Medium | 精簡的摘要、PUSH 分開核准 | 人為因素 |
| R8 | timeout 後脫離的子程序繼續寫入 | Medium | process group 加殘留檢查 | Linux 的實作 UNKNOWN |
| R9 | 撤銷本身造成資料遺失 | Medium | compare-and-swap 條件、保存修改前的內容 | — |
| R10 | secret 被 commit 進去 | Medium | 路徑拒絕清單加內容掃描 | 掃描無法完備 |
| R11 | 稽核紀錄被竄改 | Low | hash chain | 只能偵測，不能防止 |
| R12 | 違反「只有一個 writer」 | Medium | 檢查 D6 writer 的取得與釋放 | 依賴 registry 正確 |

### 9.2 Owner 開放問題（由整合者另外整理成白話版）

1. **Push 用的憑證：** F2 push 和開 draft PR 時，要用哪個憑證？
2. **核准方式：** 可以接受每次 push、每次開 PR，都由你本人在本機確認一次嗎？
3. **委派：** 本地步驟可以讓 Coordinator 代為核准嗎？
4. **push 後的撤銷：** 你偏好新增一個 revert commit，還是由你手動刪除分支？
5. **平台順序：** 先在 macOS 驗收，等 Linux 有等效沙箱之後再開放，可以嗎？

---
