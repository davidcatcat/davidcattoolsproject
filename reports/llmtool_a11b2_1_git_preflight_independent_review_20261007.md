# llmtool A11b.2.1 deterministic Git ADD preflight — 獨立 code／security review（2026-10-07）

## 範圍與方法

- **審查對象：**
  - PR #43（Draft），branch `feat/llmtool-a11b2-1-git-preflight-20261007`；
  - 目前 head `8fdf458`；
  - **審查的 implementation head 是 `a000e003298cd8ff9bd26f0d8a3892cd9882a11d`**；
  - validated publication head 是 `fc11875`；
  - base `main @ 870c32d`（merge base 相同）。
- **逐行閱讀：**
  - `git_mutation.py`（576 行）；
  - `test_git_mutation.py`（870 行，89 個 test）；
  - validation report、coordinator review 和 re-review、spec 及兩份 spec review。

  並且對照 `git_intel.py`、`action_contract.py`、QG-S1 和 `cli.py`。
- **執行：**
  - 在**整個 process 層級封鎖網路**的條件下，用 Python 3.9.6 和 3.13 執行 `test_git_mutation`、`test_action_contract`、`test_git_intel`：**180 個 test，OK，skipped=3**。skip 的原因是大小寫不敏感的檔案系統無法建立 case collision，以及無法建立非 UTF-8 的檔名，都是 host 的限制。
  - 自己撰寫 counterexample：所有 repo 和 script 都放在 scratchpad 中（`mktemp -d`），沒有修改任何 production 或 test 檔案。
- **沒有做的事：**
  - 沒有修改 production、test 或 spec；
  - 沒有實作任何修正；
  - 沒有呼叫 company 或 Codex；
  - 沒有 merge；
  - 沒有開始 A11b.2.2 或 A11b.3。

## 結論：PASS，沒有 blocking finding

**回答主要問題：YES。** 實作忠實地實現了經過 review 的 ADD-only preflight spec，**沒有**建立以下任何一種路徑：

- 可以使用的 mutation 路徑；
- 執行 repository code 的路徑；
- 不安全的 Git config 路徑；
- 逃出檔案系統邊界的路徑；
- 繞過 approval 的路徑。

finding 只有 2 個 LOW 和 6 個 NOTE。

## 1. Diff 和 scope：PASS

- `870c32d..a000e00` 修改的檔案：
  - `git_mutation.py`（新增）；
  - `test_git_mutation.py`（新增）；
  - 兩份 report。
- `a000e00..fc11875..8fdf458` 之間**只有 `reports/` 的修改**。`git diff a000e00 HEAD -- tools/llmtool/` 是空的。
- `git_mutation`、`preflight_git_add`、`GitAddPreflight` **只出現在 `git_mutation.py` 本身**（用 grep 搜尋整個 package）。
- CLI 實際測試：`llmtool add x`、`llmtool git add x`、`llmtool git-add x` 都是 **rc=2，沒有 traceback**。`cli.py` 中沒有任何 add 或 mutation 的 subparser。
- `action_contract.py` 沒有被修改，`GitOperation` 仍然只有 ADD。

## 2. 沒有 mutation：PASS

- 整個 production 檔案中，`subprocess.Popen` **只出現在 `_Reads.query`**。沒有 `os.write`、`unlink`、`rename`、`O_WRONLY` 或 `O_CREAT`。
- `future_add_argv` 只被**建構**之後回傳（第 569 行），**從來沒有被執行**。
- 沒有寫入 index、沒有刪除 lock、沒有 reset／restore／clean、沒有修改 config、沒有寫入 object。`git_blob_oid` 只在 Python 中計算 hash。

## 3. 固定的 Git query 邊界：PASS

- `_Read` 是一個 enum，`_Reads.query` 只接受 `_Read`（第 306 行），命令來自固定的 dict。**沒有任何 argv 參數**，也沒有 `run_git(args)`。
- path **只來自重新 parse 過的 A11b.1 `action.payload.files`**，而且一定放在 `--` 之後（ATTRIBUTES 和 IGNORE）。CONFIG 的 `--file` path 來自 `canonical_root`，已經符合 grammar，並且確認過等於 realpath。
- 設定：`shell=False`、stdin 是 DEVNULL、stdout 上限 1 MiB、stderr 上限 64 KiB、timeout 10 秒、`start_new_session=True`、`close_fds=True`。
- stderr **只會計算 byte 數，不會保存內容**。`GitPreflightError` 只包含固定的 category（第 49–53 行）。
- **repo 或 config 的內容進入 argv 的路徑：不存在。** config 的值只用在 `_check_config` 的比對中，不會被放進 argv 或錯誤訊息。

## 4. 環境變數邊界：PASS

`_environment` 是一個**全新的 dict**，只包含：

- 從環境繼承的 `PATH`（和 A5 相同）；
- `LC_ALL`；
- 程式產生的絕對路徑 `GIT_DIR=<root>/.git` 和 `GIT_WORK_TREE=<root>`；
- `GIT_CONFIG_NOSYSTEM`、`GIT_CONFIG_GLOBAL=/dev/null`、`GIT_ATTR_NOSYSTEM`、`GIT_NO_LAZY_FETCH`、`GIT_TERMINAL_PROMPT=0`、`GIT_LITERAL_PATHSPECS`、`GIT_PAGER`／`PAGER`；
- 唯讀時另外設定 `GIT_OPTIONAL_LOCKS=0`。

以下這些變數**都無法進入子 process**：`HOME`、ambient 的 `GIT_DIR`／`GIT_WORK_TREE`／`GIT_INDEX_FILE`、`GIT_CONFIG_COUNT`／`KEY_*`／`VALUE_*`、`GIT_CONFIG_PARAMETERS`、`SSH_AUTH_SOCK`、askpass、proxy、`DYLD_*`、`LD_*`、API credential。`root` 是 realpath 等值、而且 dev 和 ino 都經過綁定的 `canonical_root`。

## 5. F1：core.excludesFile — CLOSED

- `_check_config` 會先把 key **轉為小寫**，所以 `core.excludesfile` 在任何大小寫下都會被拒絕（第 398、401 行）。
- 這個檢查發生在 CONFIG query 之後、**所有其他 query 之前**（第 502 行），所以在 `_Read.IGNORE` 之前就會拒絕。
- `SAFE_CONFIG` 中有 `core.excludesFile=/dev/null`（第 31 行）。`_prefix()` 會套用到**所有** query（包括 CONFIG、FORMAT、HEAD、BRANCH、TREE、ATTRIBUTES、IGNORE）和 `future_add_argv`。
- 有對應的 test：
  - 外部的路徑被拒絕，而且在拒絕之前只執行過 CONFIG；
  - 大小寫混用被拒絕；
  - FIFO 不會被跟隨；
  - 所有 read 和 future plan 都帶有 `/dev/null` 的 pin；
  - `.gitignore` 和 `.git/info/exclude` 仍然有效；
  - 符合 ignore 規則的 tracked target 不會被誤判。

  這些 test 都通過了。

## 6. Repository 的身分：PASS

- **只支援 POSIX。** euid 0 會被拒絕；要求 `canonical_root == realpath`。
- **root 的開啟方式：** 從 `/` 開始，**每一層都用 `O_DIRECTORY|O_NOFOLLOW` 開啟**（第 132–134 行）。root 的 dev 和 ino 必須等於 binding、owner 必須是 euid，而且沒有 group 或 world 的寫入權限。
- `.git` 用 `O_NOFOLLOW|O_DIRECTORY` 開啟，所以 gitfile 和 symlink 都會失敗。
- `_metadata_snapshot` 會遞迴走訪整個 `.git`，並拒絕：
  - `commondir`、`objects/info/alternates`、`info/sparse-checkout`、`worktrees`、`sharedindex.*`、`*.promisor`；
  - `index.lock`；
  - sequencer 的各種標記；
  - 所有的 symlink，以及 group 或 world 可寫入的項目，或者 nlink 大於 1 的檔案。
- config 中出現 `core.worktree`、`core.bare` 為 true、或任何 `extensions.*`（除了 objectformat）時都會拒絕。
- 這個 slice 中**能夠避免**的 path 和 fd 之間的 race 都已經處理了：root 和 target 都使用 fd，而且最後會從頭重新走訪一次。剩下的 Git 子 process 必須以 path 作為 cwd，這屬於已經接受的同一個 UID 的殘餘風險。

## 7. Config 和 include：PASS

- CONFIG query 是 `config --file <root>/.git/config --no-includes --null --list`。在執行之前，會先用 `_read_file` 確認 config 是一般檔案、屬於 euid、nlink=1、大小不超過 1 MiB，**所以不會因為 FIFO 而卡住**。
- **output 的 parse：** 格式是 `key\nvalue\0`；沒有值的 boolean 是 `key\0`，parse 之後 value 是空的。
  - `core.bare` 沒有值時代表 true，會被拒絕，正確；
  - `core.fileMode` 沒有值時也代表 true，但會被**保守地拒絕**（N2）。
- **涵蓋的 key：** include.path、includeIf.*、core.worktree、extensions.*（包括 worktreeConfig 和 partialClone）、core.excludesFile、remote.*.promisor、core.splitIndex、index.sparse、core.sparseCheckout、sparseCheckoutCone、filter.*.(clean|smudge|process|required)、core.fileMode 為 false。
- **對照 spec 的要求，沒有遺漏。** 對於固定的 read query 會使用到的其他 config：
  - `pager.<cmd>` 被 `--no-pager` 排除；
  - `alias.*` 不適用，因為 builtin 優先；
  - `trace2.*` 只從 system 或 global config 讀取，而這兩者都已經停用。

## 8. Filter、attribute 和 repository code 的執行：PASS

- production 中**完全沒有** `status`、`diff` 或 `hash-object`。程式碼的註解（第 550–553 行）明確說明不使用這些命令的原因。早期以 status 為基礎的版本已經移除。
- 6 個固定的 query 都**不會進行 content conversion**：`rev-parse`、`symbolic-ref`、`ls-tree`、`check-attr`、`ls-files --others`、`config --list`。所以不會執行 clean filter、textconv 或 diff driver。
- 和執行有關的設定都已經釘住：
  - `core.fsmonitor=false`、`core.hooksPath=/dev/null`、`--no-pager`；
  - credential、ssh、gpg 都設為空值；
  - 所有 protocol 都設為 never；
  - `maintenance.auto=false`、`gc.auto=0`。
- `check-attr` 會讀取 repo 內的 `.gitattributes` 和 `info/attributes`；global 和 system 的 attribute 已經停用。**只有 `unset` 和 `unspecified` 會通過**（第 544 行）。有 test 驗證：configured filter 被拒絕、沒有 driver 的 `filter=lfs` 被拒絕、text 和 eol 被拒絕。
- **在中途加入的 filter：** `test_concurrently_introduced_filter_never_executed` 用 marker 檔證明 filter **沒有被執行**。這個保證來自結構本身：後續的 query 不會進行 content conversion。
- **結論：preflight 期間沒有任何由 repository 控制的可執行路徑。**

## 9. Target 檔案系統的綁定：PASS

- 上層目錄從 root fd 開始，用 `O_DIRECTORY|O_NOFOLLOW` 逐層開啟，每一層都必須屬於 euid，而且沒有 group 或 world 的寫入權限。任何一層出現 `.git` 就拒絕。
- 每一層和最後的檔名，都必須在 `listdir` 中**逐 byte 存在**。
- target 用 `O_NOFOLLOW|O_NONBLOCK` 開啟，所以 FIFO 不會卡住。之後檢查：
  - 必須是一般檔案；
  - nlink=1；
  - owner 是 euid；
  - 沒有 group 或 world 的寫入權限；
  - 和 root 在同一個檔案系統上；
  - dev、ino、mode、uid、gid、size、mtime、nlink 都等於 Action 的值；
  - SHA-256 和 Action 一致。
- content 的讀取和 fstat 都**使用同一個 fd**。讀取前後的 fstat，以及用名稱重新 stat 的三組 fingerprint 必須一致。
- **case 和正規化：**
  - 大小寫不同的 alias 會被拒絕：既有 index entry 的 NFC 加上 casefold 結果相同、但 bytes 不同時拒絕；
  - 磁碟上的名稱必須和 target 逐 byte 相同；
  - A11b.1 的 grammar 會拒絕 combining mark，所以不可能出現 NFD 形式的 target。
- symlink 會因為 `O_NOFOLLOW` 而失敗；hardlink 會因為 nlink 而失敗；FIFO、socket、device、directory 都會因為型別檢查而失敗。這些都有 test。

## 10. Index parser：PASS

- **基本檢查：** 總大小上限是 2 MiB；trailer 的 checksum 會依照 object format 驗證；只接受 version 2、3、4；entry 數量上限是 20000；每一個 entry 都會檢查 cursor 是否超出 `end`。
- **flag：**
  - v2 帶有 extended flag 時拒絕；
  - extended flag 只允許 skip-worktree（0x4000）和 intent-to-add（0x2000）；
  - assume-unchanged 由 0x8000 判斷。
- **v4 的 prefix 壓縮：** varint 的寫法 `((remove+1)<<7)|low7` 和 Git 的 decode 等價。每一步都檢查 `remove <= len(previous)`；越界時 `IndexError` 或 `ValueError` 會被轉換成 reject。
- **path：**
  - 不能是空的、不能是絕對路徑、長度不超過 4096、不能有 `.`、`..` 或 `.git`；
  - mode 必須是 4 種合法值之一；
  - name length 必須正確；
  - padding 的 bytes 必須是 0；
  - path 以 bytes 保存，所以非 UTF-8 的檔名也能完整保留（因為 host 的限制，這個 test 被 skip）。
- **排序：** 要求 `(path, stage)` 嚴格遞增，這和 Git 以 memcmp 比較的順序一致。
- **extension：** 大寫開頭的 extension 會被略過；`link`、`sdir` 和其他小寫開頭的 extension 都會被拒絕；最後必須剛好結束在 `end`。
- `index.skipHash` 產生的全零 trailer 會被拒絕（N2，保守的處理）。

## 11. Index 和 HEAD 的等值政策：PASS

- 任何 stage 不是 0 → `conflict`；skip-worktree、ITA、assume-unchanged、gitlink → 拒絕。
- 用語意比較 `ls-tree -r` 的結果（blob、symlink；任何 commit 或 tree 型別都會被拒絕）和 index entry 的 `(path, mode, oid)`，兩者必須**完全相同**。
- 所以：任何已經 staged 的修改都會被拒絕，包括已經 staged 的 target、staged 加上 unstaged 的 target、rename、copy、delete，以及無關的 staged 修改。這符合 N1 的無條件 reject。
- **無關的 unstaged 和 untracked 修改可以保留**（有 test）。untracked 的授權檔案不需要 index entry 也能通過。ignored 的 untracked target 會被 IGNORE query 拒絕。

## 12. HEAD 和 repository 狀態：PASS

- **HEAD：** `rev-parse --verify HEAD^{commit}` 失敗時（unborn）會拒絕；`symbolic-ref --quiet HEAD` 失敗時（detached）會拒絕；不是 `refs/heads/` 也會拒絕；和 `expected_head` 不同時拒絕。
- **其他狀態：**
  - merge、rebase、cherry-pick、revert、bisect、sequencer 的標記會被拒絕；
  - conflict、promisor、sparse、split／shared index、submodule／gitlink 都會被拒絕。

## 13. Semantic evidence：PASS

- `GitAddPreflight` 是 frozen dataclass，包含：
  - `action_digest`、`repository_root`、`root_device`、`root_inode`；
  - `head`、`object_format`、`index_sha256`、`paths`、`state_digest`；
  - `semantic_index`（`IndexEntry` 的 tuple）、`bound_blobs`；
  - `future_add_argv`、`future_environment`。
- `state_digest = action.expected_state.digest`（第 568 行）。
- 不包含任何 stderr、config 或檔案內容。

## 14. Blob OID 和 mode：PASS

- 計算方式是 `hashlib.new(object_format, b'blob ' + len + b'\0' + content)`。對 sha1 和 sha256 都是正確的，而且有 SHA-256 repo 的 test。
- mode 的預測：owner 的執行位元 0o100 → 100755，否則 → 100644。這和 Git 的 `ce_mode_from_stat` 一致。

## 15. TOCTOU 和最後的重新驗證：PASS

- **最後的重新驗證**（第 555–565 行）包括：
  - 重新走訪 root；
  - `.git` 的 fingerprint；
  - **完整的 `.git` metadata snapshot**（包括 config、info/attributes、index 的 mtime、ctime 和 size）；
  - 重新 hash index；
  - 重新取得 `.gitattributes` 的 snapshot；
  - 重新綁定 target；
  - HEAD 和 branch。
- **我獨立驗證了三種單一 drift**（scratchpad 中的 `test_a11b21_extra`，繼承 fixture）：
  - **X1：** ATTRIBUTES 之後只修改 `.git/config`，加入一個無害的 `user.name` → `repository identity mismatch`；
  - **X2：** ATTRIBUTES 之後只新增 `.git/info/attributes` → reject；
  - **X3：** IGNORE 之後只執行 `git read-tree HEAD` 重寫 index → reject；
  - **X4：** 沒有修改時的 control → PASS。

  所以 config 的 drift **確實會被 `.git` metadata snapshot 偵測到**，不需要依賴 `.gitattributes` 的變化。
- **中途加入的 filter 無法讓檢查程式本身執行它：** 後續的 query 都不會進行 content conversion（§8）。

## 16. Read process 的清除：PASS（附 L1）

- timeout、output 上限、selector 的關閉、process group 都和 A5 一致。`returncode is None` 時會 `killpg(SIGKILL)`，然後 `wait()`。
- 有一個實際的處理不一致（L1，見下方）。沒有實際的 fd 洩漏（GC 會回收 pipe），但這和 A5 不同。

## 17. Public 和 import 邊界：PASS（附 N4）

- 用 AST 測試 production 的 import，並實際 grep 整個 package，都確認沒有其他地方 import 這個模組。CLI 中沒有 add 指令，rc=2，沒有 traceback。

## 18. 測試品質：PASS（附 L2）

- 大部分的安全性 test 都使用**彼此獨立的 fixture**。檔案末尾的 `_config_test` 每一個 key 都建立新的 repo，並斷言 index 的 bytes 沒有改變。
- 下列幾類 test 都有實際的探測：filter（marker 檔）、FIFO（config、include、excludes）、外部的值不會出現在 argv 或錯誤中、SHA-256 repo、import 邊界。
- 例外見 L2。

## 19. Validation 和 CI：PASS

- run `37530812194`（pull_request）和 `37530804250`（push）的 `headSha` **都是 `fc11875b…`**，而且 **conclusion 都是 success**。
- `fc11875` 和 `a000e00` 相比只有 report 的修改，所以 production 和 test 的 bytes 和 `a000e00` 完全相同。
- `8fdf458` 只修改 report，被 path filter 排除，所以不需要另外跑 application CI。

## 20. B5 的區隔

**A11b.2.1 的 PASS 不代表 A11b.2.2 已經被授權。** B5 仍然是 A11b.2.2 的 blocker，內容包括：

- mutation 的 process containment；
- SIGTERM、SIGHUP、SIGINT 的處理；
- leader 結束之後的 descendant；
- macOS 上 parent 被 SIGKILL 的處理方式；
- 和 durable-before-action audit 的互動。

L1 也應該在這時一起處理。

---

## Findings

### L1 — LOW — `git_mutation.py` / `_Reads.query` 的 `finally`（第 350–358 行）

- **問題：** cleanup 只捕捉 `ProcessLookupError`。在 macOS 上，對一個 **leader 已經結束但還沒有被 reap** 的 process group 呼叫 `killpg`，會產生 **`PermissionError`**。A5 的 `git_intel.py` 第 141–144 行明確處理了這個情況（`except (ProcessLookupError, PermissionError): process.kill()`），這裡沒有沿用。
- **影響：** `PermissionError` 會在 `finally` 中**取代原本的 `GitPreflightError`**，而且會略過後面的 `stdout.close()` 和 `stderr.close()`。
  - 在 `preflight_git_add` 中，它會被外層的 `except OSError` 轉換成 `GitPreflightError(category)`。**仍然是 fail closed，但 category 會變成錯誤的類別**（例如 `unsupported repo layout`，而不是 `Git inspection unavailable`）。
  - pipe 要等到 GC 才會被關閉。
  - 沒有安全性的影響。但 A11b.2.2 很可能會重用這個 runner，這時就必須處理。
- **重現：**
  1. 獨立驗證：`/usr/bin/true` 加上 `start_new_session`，等 0.3 秒之後 `killpg` → `PermissionError [Errno 1]`。
  2. 透過實際的 `_Reads.query(_Read.CONFIG)`：把 `METADATA_LIMIT` 設為 16（模擬 output 超過上限），並讓 read 變慢，使子 process 在被發現超過上限之前就已經結束。結果是 **40 次中有 40 次**丟出 raw `PermissionError`，fd 的差額是 0。
- **修正（建議在 A11b.2.2 之前，或者這個 PR 的 follow-up 中處理）：** 和 A5 一樣捕捉 `(ProcessLookupError, PermissionError)`，必要時呼叫 `process.kill()`。用巢狀的 `try/finally` 保證 pipe 一定會被關閉。加入一個 test，強制走 output 超過上限的路徑，並斷言丟出的是 `GitPreflightError('Git inspection unavailable')`。

### L2 — LOW — `test_git_mutation.py` / `test_mid_query_instruction_file_index_root_and_config_drift`、`test_concurrently_introduced_filter_never_executed`

- **問題：**
  - 前者的名稱宣稱涵蓋「instruction file、index、root 和 config 的 drift」，但實際上**只修改了 target 的內容**；
  - 後者在 ATTRIBUTES 之後**同時**修改了 `.gitattributes` 和 config，所以 reject 可能來自 attribute 的 snapshot，**無法單獨證明** config 的 drift 會被偵測到。
- **影響：** 這是測試品質的問題。實作本身是正確的：我的 X1、X2、X3 分別證明了只修改 config、只修改 info/attributes、只修改 index 的 drift 都會被偵測到。
- **修正：** 把它們拆成單一變數的 drift test：只修改 config、只修改 `.git/info/attributes`、只修改 index、只修改 root；並且修正 test 的名稱。

### NOTE

- **N1：** `_metadata_snapshot` 要求整個 `.git` 中的項目都屬於 euid、沒有 group 或 world 的寫入權限、nlink=1、沒有 symlink，而且總數不超過 20000。下列 repo 都會被**保守地拒絕**：
  - 用 `git clone --local` 建立的 repo（object 是 hardlink）；
  - `core.sharedRepository=group`；
  - loose object 非常多的 repo。

  這是易用性的限制，不影響安全性。目前的 canonical repo 中 `.git` 有 3,553 個項目，沒有 hardlink，所以可以使用。
- **N2：** 沒有值的 `core.fileMode`（代表 true），以及 `index.skipHash` 產生的全零 trailer，都會被保守地拒絕。這是 fail closed 的處理，可以接受。
- **N3：** 最後的重新驗證不包含工作目錄中 `.gitignore` 的 snapshot。在這段時間內修改 `.gitignore`，只會讓將來的 add 拒絕 ignored 的檔案，**沒有安全性的影響**。
- **N4：** import 邊界的 test 只檢查 AST 中的 import 敘述，無法偵測 `importlib.import_module('…git_mutation')` 這種以字串 import 的方式。可以考慮再加上一個 grep，檢查 production 中是否出現 `git_mutation` 這個字串。
- **N5：** `_read_file` 會先用 `O_NONBLOCK` 開啟檔案，之後才檢查型別。理論上，開啟某些 device 可能有副作用。但 target 都在 repo 內，而且 FIFO 和 socket 都已經有 test，實際上可以忽略。可以考慮先 lstat。
- **N6：** read runner 的 `killpg` 只在 leader 還沒有被 reap 時才執行（和 A5 相同）。唯讀的 query 不會產生 descendant，所以這個 slice 可以接受。在 mutation 的情境下，這屬於 B5 的範圍。

## 判定

```text
A11b.2.1 INDEPENDENT CODE/SECURITY REVIEW:
PASS

Reviewed implementation head:
a000e003298cd8ff9bd26f0d8a3892cd9882a11d

Validated publication head:
fc11875b45b07aab94f12a0aa9b1da7df3ced3a3

Scope ADD-preflight only:
PASS

git add execution:
NOT IMPLEMENTED

Public mutation path:
NONE

Repository identity/layout:
PASS

Config/include boundary:
PASS

core.excludesFile F1:
CLOSED

Filter/attribute execution boundary:
PASS

Target filesystem binding:
PASS

Index parser/state policy:
PASS

HEAD/state binding:
PASS

Semantic evidence/blob OID:
PASS

TOCTOU revalidation:
PASS

Read-process containment:
PASS (L1 LOW: killpg PermissionError not handled as in A5 — fail-closed but miscategorised; fix before A11b.2.2 reuse)

Approval boundary:
PASS

Test quality:
PASS (L2 LOW: two compound/misnamed drift tests; implementation independently verified correct)

Final-head CI:
PASS

New blockers:
NONE

A11b.2.2 B5:
STILL BLOCKING A11b.2.2

Ready to merge PR #43:
YES
```

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #43 的 branch。沒有修改 production、test 或 spec，沒有 merge，也沒有開始 A11b.2.2 或 A11b.3。
- counterexample 的 repo 和 script 只放在 session 的 scratchpad 中，沒有刪除，也沒有加入 Git。
