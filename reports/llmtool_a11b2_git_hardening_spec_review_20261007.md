# llmtool A11b.2 Git Hardening implementation spec — design／security review（2026-10-07）

## 範圍與方法

- **審查對象：**
  - PR #42，branch `docs/llmtool-a11b2-git-hardening-spec-20261007`，**exact head `2fc0f944803a23c3ed63fb1f3905b08a8148f6ce`**；
  - base `main @ 54ef8f6c`，merge base 相同；
  - 只有一個檔案：spec，680 行。
- **完整閱讀 spec**，並逐項對照：
  - `git_intel.py`：A5 runner、`SAFE_CONFIG`、`_environment`、filter 和 promisor 的偵測、process group 的處理；
  - `action_contract.py`：A11b.1 的 path grammar、`FileMetadata`、`GitAddPayload`、`Action.expected_state`、`ApprovalValidation`；
  - QG-S1 §17 的 R-EXEC、R-FS、R-GIT、R-APR、R-INJ、R-AUD；
  - ROADMAP 的 A11b.2 段落；
  - `skills/safe-git`。
- **實際驗證：** 在 scratchpad 中用 `mktemp -d` 建立**可丟棄的 repo**，以 Apple Git 2.39.5 和 A5 的環境變數（`GIT_CONFIG_NOSYSTEM`、`GLOBAL=/dev/null`、`ATTR_NOSYSTEM`、`LITERAL_PATHSPECS`）實際驗證 5 個 `git add` 的行為（§3）。沒有碰觸任何真實的 repo。
- **沒有做的事：**
  - 沒有修改 spec 或程式碼；
  - 沒有呼叫 company 或 Codex；
  - 沒有 merge；
  - 沒有開始 A11b.2 或 A11b.3。

## 結論：BLOCKED（只需要文件修正）

- spec 的骨架是對的：ADD-only、沒有 public path、兩階段驗證、approval 邊界、分段實作。
- 但是**有 4 個會直接寫進 A11b.2.1 preflight 的規則是錯的或沒有決定**（B1–B4），其中 **B1 會造成機密外洩**。
- 這些都可以用一份 **spec addendum（只修改文件）** 解決。addendum 經過 targeted 確認之後，A11b.2.1 就可以開始。
- B5（process containment 的細節）只阻擋 A11b.2.2，不阻擋 A11b.2.1。

## 1. Scope：PASS

- A11b.1 的 `GitOperation` 只有 `ADD`（`action_contract.py` 第 171–172 行）。`GitAddPayload` 要求每個檔案都存在（第 318–319 行），所以 deletion 無法表示。spec §3 明確寫出「must not widen the action schema / no command string」。
- §1 列出的非目標（commit、apply、checkout、reset、clean、branch／tag、merge／rebase、fetch／pull、push、remote、submodule、任意 argv、shell）**和本次 review 要求的清單完全一致**。§4 禁止公開 `run_git(args)`、任意 argv、env、cwd 或 subcommand。
- 沒有提出任何暫時性的 approval token 或 debug flag（§14）。

## 2. 沒有 public mutation path：PASS（附 1 個 test 要求）

- §1、§13、§14 都寫明：primitive 只供內部和 test 使用；在 durable-before-action audit 和真正的授權機制出現之前，不能新增 public 入口。這是合理的，也符合 QG-S1 R-AUD-4。
- **缺少：** 沒有任何 test 能**證明**這一點。建議在 test matrix 中加入一個 **import／AST 邊界 test**：
  - 除了 `tests/` 之外，任何 production 模組（`cli`、`entrypoint`、`chat`、`orchestration`、`benchmark` 等）都不能 import 這個 mutation 模組；
  - 不能有任何 argparse subcommand 參照它。

  這是 R5（見 §B 之後的建議清單）。

## 3. 實際驗證的結果

| # | 驗證內容（disposable repo） | 結果 | 對 spec 的影響 |
|---|---|---|---|
| 1 | `.gitattributes: f.bin filter=demo`，clean filter 把 `secret` 換成 `POINTER`。正常的 `git add` 和 A5 式的 `-c filter.demo.clean=` 比較 | 正常的 add staged `POINTER-large-binary`；**中和之後 staged 的是 `secret-large-binary`** | **B1**：對 mutation 中和 filter，會把 repo 原本要經過 clean filter 轉換的內容以**原文**放進 index |
| 1b | 只有 attribute `filter=lfs`，**沒有設定 driver** | exit 0，**直接 staged 原文** | B1：只檢查 config 不夠，必須檢查 attribute |
| 2 | `.gitattributes: t.txt text eol=lf`，worktree 是 CRLF，已經設定 `core.attributesFile=/dev/null` 和 `core.autocrlf=false` | worktree 有 CR，**staged 沒有 CR** | **B2**：repo 內的 `.gitattributes` 和 `.git/info/attributes` 不受 A5 的設定影響，staged 的 blob 不等於綁定的 `content_sha256` |
| 3 | `.git/hooks/post-index-change` | 預設的 `git add` **會執行這個 hook**；`core.hooksPath=/dev/null` 之後不會執行 | 證明 hooks 的中和是必要的（`post-index-change` 正是 add 會觸發的 hook），應該列為具名的 test |
| 4 | 一個無關的 tracked 檔案只有 mtime 改變，然後 `git add` 另一個檔案 | 無關 entry 的 `ls-files -s` **沒有改變**；**raw index 的 SHA 改變了** | **B4**：post-verify 不能用 raw bytes 判斷「無關的 entry 沒有改變」 |
| 5 | repo config 中的 `core.worktree=<另一個目錄>` | **staged 的是另一個目錄中的內容（`evil`）**；`--show-toplevel` 回報的是那個目錄 | **B3**：必須明確釘住 git dir 和 worktree，或者拒絕 `core.worktree` |

## Blockers

### B1（A11b.2.1 之前，安全性）：mutation 不能中和 filter，必須拒絕

- spec §9 寫著：*"Filter keys discovered by the hardened preflight must be neutralized **exactly as A5 does**"*。
- A5 是唯讀的檢查，中和 filter 只會讓 diff 的顯示不一樣。但是 **`git add` 會把中和後的結果寫進 index**。實際驗證 1 證明：中和之後 staged 的是**未經 clean filter 處理的原文**。
- 實際的後果：
  - **git-crypt** 這類工具的 clean filter 負責**加密**。中和之後會把**明文的機密**放進 index，下一次 commit 和 push 就會外洩；
  - **git-lfs** 的 clean filter 負責產生 pointer。中和之後會把整個大型二進位檔放進 index，破壞 repo。
- 實際驗證 1b 也證明：即使**沒有設定 driver**，只要 attribute 中有 `filter=…`，add 就會直接 staged 原文。
- **必須修正為：** 對 ADD 而言，以下情況都在 preflight 時 **reject，而不是 neutralize**：
  - 任何 scope 中設定了 `filter.<driver>.(clean|smudge|process|required)`；
  - **或者**任何 target path 的 `filter` attribute 是 set 或任何值（使用 hardened 的 `git check-attr -z filter -- <paths>` 判斷）。

  A5 的唯讀中和方式維持不變。

### B2（A11b.2.1 之前，資料完整性）：會轉換內容的 attribute 和 config 必須拒絕，並且驗證 staged blob

- 實際驗證 2：`text`／`eol` 這類 attribute，在設定 `core.attributesFile=/dev/null` 之後**仍然有效**，因為 repo 內的 `.gitattributes` 和 `.git/info/attributes` 不受影響。結果是 staged 的 blob 和 FileExpectation 的 `content_sha256` 不同。
- **必須修正為：**
  1. **preflight 時 reject：** target 的 `text`、`eol`、`crlf`、`ident`、`working-tree-encoding`、`filter` 等 attribute 有任何設定（unset 或 unspecified 以外的值）；
  2. **釘住設定：** `core.autocrlf=false`、`core.safecrlf=false`、`add.ignoreErrors=false`。另外，`core.fileMode` 如果被設定為 false 就 reject，否則 staged 的 mode 不會反映 bound 的執行權限；
  3. **post-verify 的強證明：** 對每一個 target，staged entry 的 blob OID 必須等於「**綁定的原始 bytes**」依照 repo 的 object format（sha1 或 sha256，對應 `expected_head` 的長度）計算出的 `blob <len>\0<content>` hash；staged 的 mode（100644／100755）必須和 bound `FileMetadata.mode` 的執行位元一致。

  這一點可以把「only authorized content staged」從推論變成**確定性的證明**。

### B3（A11b.2.1 之前）：repository layout 必須完全釘住，不能交給實作時再決定

spec §5 的第 9 項和 §6 的 sparse 都寫著「unless implementation review proves … default fail closed」。對這個安全邊界來說，應該在 spec 中**直接決定為拒絕**。必須明確寫出下列規則：

1. **root：**
   - `canonical_root` 必須**等於** `os.path.realpath(canonical_root)`，也就是路徑中沒有任何 symlink；
   - 用 `O_DIRECTORY|O_NOFOLLOW` 開啟 root，然後 fstat 檢查 dev 和 ino；
   - root 必須由 euid 擁有；
   - 以 root 身分執行時 reject。
2. **`.git` 必須是真正的目錄：** 用 lstat 檢查，不能是 gitfile（linked worktree 或 submodule 的形式）或 symlink；不能有 `commondir`；不能有 `objects/info/alternates`（建議 reject，簡化後續的分析）。
3. **每一個 Git 呼叫都明確設定** `GIT_DIR=<root>/.git` 和 `GIT_WORK_TREE=<root>`（絕對路徑，由程式產生，不從環境繼承）。這樣可以讓 `core.worktree` 失效（實際驗證 5）並避免向上層目錄搜尋 repo。另外，如果 config 中設定了 `core.worktree` 或 `core.bare=true`，**直接 reject**。
4. **index：**
   - 位置固定為 `<root>/.git/index`。用 `O_NOFOLLOW` 開啟，必須是一般檔案而且 nlink=1；
   - **index 不存在時 reject**（HEAD 存在的一般 repo 一定會有 index）；
   - **`index.lock` 存在時 reject，而且絕對不能刪除它**；
   - **split index 時 reject**：`core.splitIndex=true`、`sharedindex.*` 檔案，或 index 中有 `link` extension；
   - **sparse index 或 sparse checkout 時 reject**：`index.sparse`、`core.sparseCheckout`、`info/sparse-checkout`，或 `sdir` extension。
5. **index entry 的狀態：** repo 中任何地方只要有 **skip-worktree、assume-unchanged 或 intent-to-add** 的 entry 就 reject（用 `ls-files -t -v -z` 或解析 index 判斷）。這些 entry 會讓 add 靜默地不做事，或者產生和預期不同的 staging。
6. **status 必須關閉 rename 偵測：** A5 的 `SAFE_CONFIG` 中是 `status.renames=true`。mutation 的 preflight 必須使用 `--no-renames` 或 `status.renames=false`，才能得到每個 path 各自的狀態。
7. **target 的上層目錄中不能有 `.git`**（巢狀的 repo 或 submodule 的工作目錄），否則 reject。
8. **ignored 的 target**（untracked 而且符合 ignore 規則）在 preflight 時 reject。不能使用 `-f`。
9. **對 index hash 的兩個欄位：** `Action.expected_state.index_sha256` 是由 `payload.expected_index_sha256` **推導出來的**（`action_contract.py` 第 355–359 行），不可能互相矛盾。spec §8 的「disagreement → reject」可以保留作為縱深防禦，但 test 不需要假設這種情況會發生。
10. **index 的 hash 算法**（`<root>/.git/index` 的 raw bytes 做 SHA-256）必須由 **A11b.2.1 提供唯一的函式**，將來產生 Action 的程式也必須使用同一個函式。

### B4（A11b.2.1 之前）：post-verify 的定義必須是語意上的比對，不是 raw bytes

- 實際驗證 4：`git add` 會重寫整個 index，**無關 entry 的 stat 資料也會改變**，所以 raw bytes 的比對無法證明「無關的部分沒有改變」。
- **必須定義為：** pre 和 post 的 stage-0 entry 集合，都用 `(path bytes, mode, OID, stage, skip-worktree／intent-to-add flags)` 比對：
  - **post 必須完全等於「pre 的集合，換掉授權的 target 之後」的結果**；
  - 其他每一個 entry 的上述欄位都必須和 pre 完全相同；
  - 不能有新的 path；
  - target 的 path bytes **必須完全相等**。macOS 上 `core.ignoreCase` 和 `core.precomposeUnicode` 的預設是 true，add 可能會更新大小寫或正規化形式不同的既有 entry。
- **preflight 也要做相同的要求：** target 在磁碟上的名稱必須和 `os.listdir(parent)` 的結果**逐 byte 相同**，不能只依賴 stat 成功，因為在大小寫不敏感的檔案系統上，`README.MD` 也可以 stat 成功。

### B5（A11b.2.2 之前，不阻擋 2.1）：process containment 必須寫出機制

- **A5 runner 的實際缺口：** `finally` 中只有在 `process.poll() is None`（也就是 leader 還活著）時才 killpg（`git_intel.py` 第 138–145 行）。如果 leader 已經結束，但 descendant 還在同一個 group 中，就**不會被清除**。這正是 spec 要求的「normal parent shutdown if the child remains alive」這個情況。
- **SIGTERM 和 SIGHUP：** Python 預設會直接結束 process，**不會執行 finally**。而且子 process 用了 `start_new_session=True`，不會收到 terminal 的 SIGHUP。結果就是 `git add` 變成 orphan，在 parent 已經失敗之後**仍然繼續 staging**，並持有 `index.lock`。
- **parent 被 SIGKILL 或 crash**（QG-S1 R-EXEC-6 的「llmtool crash」）：killpg 無法處理。macOS 沒有 `PR_SET_PDEATHSIG`。
- **必須在 A11b.2.2 的 spec 中寫明：**
  1. leader 結束之後**一律 killpg**（忽略 `ProcessLookupError`）；
  2. 只在 mutation 期間安裝 SIGTERM／SIGHUP／SIGINT handler，把 signal 轉換成例外，走同一個清除路徑；結束後恢復原本的 handler；
  3. **parent SIGKILL 的處理方式要選定一種**：
     - (a) 使用 supervisor 或 kqueue 監看 parent 的機制；
     - (b) 明確列為殘餘風險：git add 本身是短暫的，所有 helper 都已經被拒絕或中和；後續的 recovery 會透過 index hash 和 entry 比對，偵測到「沒有完成驗證的 staging」。

     選 (b) 時，必須在 R-EXEC-6 的對照中寫明，並由 security review 接受；
  4. 在所有 helper 都被拒絕的前提下，**預期的子 process tree 只有單一的 git**。test 應該斷言沒有任何 descendant。killpg 無法處理呼叫 `setsid` 的 descendant，但 fsmonitor daemon 這類會 daemonize 的 helper 已經被 `core.fsmonitor=false` 排除，所以這個前提要寫明。

## 各項目的判定

| # | 項目 | 判定 | 說明 |
|---|---|---|---|
| 1 | Scope ADD-only | PASS | §1 |
| 2 | 沒有 public mutation path | PASS | 加入 R5 的 import 邊界 test |
| 3 | A5 hardening 的繼承 | **finding** | env、`-c`、protocol、maintenance、promisor、bounded、timeout、process group 都有涵蓋。但是 (a) filter 對 mutation 必須是 reject（B1）；(b) A5 的 `status.renames=true` 不適合 mutation 的 preflight（B3-6）；(c) A5 的 killpg 條件有缺口（B5）；(d) `GIT_OPTIONAL_LOCKS`：mutation 的 add 不能設定為 0，preflight 必須設定為 1，spec §10 已經寫明，正確 |
| 4 | Repository／HEAD／index 綁定 | **finding** | B3 |
| 5 | 精確的 dirty-state 政策 | **finding（政策正確，細節不足）** | 「不全面拒絕 dirty，但要求 exact scope」的方向是**正確的**：ADD 本來就是 staging 已經存在的修改。必須 fail closed 的情況：target 已經 staged 而且還有 unstaged 的修改；intent-to-add；rename／copy（關閉 rename 偵測之後逐 path 判斷）；deletion（contract 已經無法表示）；只有 mode 改變（以 B2 的 mode 驗證處理）；ignored；sparse；assume-unchanged；skip-worktree；submodule／gitlink。這些都已經放進 B2、B3、B4 |
| 6 | Path、symlink、hardlink、TOCTOU | **finding** | A11b.1 的 grammar 已經拒絕以 `-` 開頭的 component、`..`、`.git` 和 credential 名稱，也只允許 alnum 和 `._-`（NFD 的 combining mark 不是 alnum，會被拒絕），**對這個 executor 來說足夠**。spec 的 dash test 應該改為「contract 層拒絕」加上 `--` 的縱深防禦。**缺少：** R-FS-2 的 **dir-fd 和 `O_NOFOLLOW`**：每一層都用 `openat(O_DIRECTORY|O_NOFOLLOW)`，target 用 `O_NOFOLLOW` 開啟之後，**在同一個 fd 上 fstat 並計算 hash**，避免 stat 和 open 之間的 race；R-FS-3 的**上層目錄 owner 和 mode**（必須屬於 euid，不能有 group 或 world 寫入權限）；B4 的 exact 名稱 |
| 7 | 惡意 Git config 和 helper 的抑制 | **finding** | 對 `git add` 來說，`credential.helper`、`core.sshCommand`、`gpg.program`、`commit/tag.gpgSign` **實際上都不會被呼叫**（add 不使用 network，也不簽章）。可以保留作為縱深防禦，但**不足夠**。真正相關的是：filter 和 attribute（B1、B2）、fsmonitor（已經釘住）、**`post-index-change` hook**（已經由 hooksPath 處理，加入具名的 test）、`core.worktree`（B3）、`add.ignoreErrors`、`core.autocrlf`、`core.safecrlf`、`core.fileMode`（B2）、split／sparse index（B3）、`include.path` 和 `includeIf`（被 include 的 config 中的 filter 或 worktree 必須被偵測到，加入 test）。`trace2.*` 只從 system 或 global config 讀取，已經被 NOSYSTEM 和 GLOBAL 排除。**同意不使用 `safe.directory=*`** |
| 8 | Process-tree containment | **finding** | B5（阻擋 2.2） |
| 9 | Approval 邊界 | PASS | §14 完整保留：`bindings_valid` 不等於 authorized；`source=USER` 不等於身分驗證；model 說 approved 不算數；沒有 bypass。A11b.1 的 `ApprovalValidation.authorized` 一定是 False（`action_contract.py` 第 445–449 行）。建議 R6：`execute_git_add` 的 docstring 和 test 寫明，它**不會讀取**任何 `ApprovalValidation` |
| 10 | Audit handoff | **finding（輕微）** | §13 列出的欄位是正確的，但 §4 的 dataclass **沒有**包含 `action_digest`、`root_device` 和 `root_inode`、object format、post-verify 的結果碼。而且失敗的路徑（nonzero exit、post-verify 失敗）只說「return failure and leave evidence」，沒有定義型別。建議 R7：定義帶有 before／after evidence 的 typed failure result；`state_digest` 直接使用 A11b.1 的 `Action.expected_state.digest`。「在 durable audit 出現之前不開放 public mutation path」：**可以接受** |
| 11 | Test matrix | **finding** | 見下方的 R8 |
| 12 | 實作的切分 | PASS | 2.1 → 2.2 → 2.3 的順序正確。2.1 只做 preflight，不做 mutation，**不會**自動授權 2.2。如果 2.1 發現設計問題，必須回到 spec |

## 建議（non-blocking，但應該一起寫進 addendum）

- **R5：** import 和 CLI 邊界的 test（見第 2 項）。
- **R6：** primitive 不讀取 approval。
- **R7：** 補齊 audit evidence 的欄位，並定義 typed failure result。
- **R8：** test matrix 需要補上的 adversarial cases，每一個都對應上面的具體缺口：
  - git-crypt 式的**加密** clean filter，以及沒有 driver 的 `filter=lfs` attribute → preflight 拒絕，index 沒有被修改（B1）；
  - `text eol=lf` 或 `ident` 或 `working-tree-encoding` 的 attribute → 拒絕；staged blob OID 不等於 bound bytes 的 hash 時，post-verify 必須 FAIL（B2）；
  - **`post-index-change` hook** 不會執行；
  - **被 include 的 config**（`include.path` 指向 repo 內的檔案，其中有 filter 或 `core.worktree`）→ 被偵測並拒絕；
  - `core.worktree` 重導向；`.git` 是 gitfile 或 symlink；有 `commondir` 或 `alternates`（B3）；
  - `index.lock` 已經存在 → 拒絕，而且 lock 仍然存在；
  - split index、sparse index、skip-worktree、assume-unchanged、intent-to-add；
  - 大小寫或 NFC／NFD 不同的 target 名稱；在 macOS 上，`ignoreCase=true` 時不能更新到其他大小寫的 entry（B4）；
  - 無關的 tracked 檔案只改變了 stat → post-verify 仍然 PASS；無關 entry 的 OID 被並行地修改 → post-verify FAIL（B4）；
  - 上層目錄有 group 或 world 寫入權限 → 拒絕；
  - leader 結束但 descendant 還活著 → killpg 清除；SIGTERM 和 SIGHUP 實際送給 parent 的 test（B5）；
  - mutation 模組只能被 `tests/` import（R5）。
- **R9：** 寫出威脅模型的邊界：同一個 UID 的並行惡意 process 可以在任何檢查之後修改檔案（path 型的 cwd 無法完全避免 race），這**不在** A11b.2 可以防禦的範圍內。A11b.2 防禦的是：repo 內容和 model 輸出的 injection、意外的並行修改、以及惡意的 repo config 和 attribute。post-verify 用來偵測殘餘的 race。
- **R10：** QG-S1 的 R-EXEC-5（執行 repository code 時需要 OS sandbox）：在對照表中寫明，A11b.2 的 `git add` 在 B1、B2、B3 和 hooks／fsmonitor 都被拒絕或中和之後，**不會執行任何 repository code**，所以不需要 sandbox。這個前提必須由 test 證明。

## 判定

```text
A11b.2 SPEC REVIEW:
BLOCKED

Exact review head:
2fc0f944803a23c3ed63fb1f3905b08a8148f6ce

Scope ADD-only:
PASS

No public mutation path:
PASS (add R5 import/CLI boundary test)

A5 hardening inheritance:
finding — env/-c/protocol/maintenance/promisor/bounds/timeout/process-group carried; but filters must be REJECTED for
  mutation (B1), status.renames=true unsuitable for mutation preflight (B3-6), A5 killpg only when leader alive (B5)

Repository/HEAD/index binding:
finding (B3) — decide, not defer: realpath-exact root + O_NOFOLLOW fstat; .git real dir (no gitfile/symlink/commondir/
  alternates); explicit GIT_DIR/GIT_WORK_TREE; reject core.worktree/bare; index at <root>/.git/index, absent → reject,
  index.lock → reject (never delete), split/sparse index → reject; single shared index-hash function

Exact dirty-state policy:
finding — exact-scope policy is sound; fail closed on pre-staged target, intent-to-add, skip-worktree, assume-unchanged,
  ignored target, nested .git, sparse, submodule; disable rename detection in preflight

Path/symlink/hardlink/TOCTOU:
finding — A11b.1 grammar sufficient; add dir-fd/O_NOFOLLOW walk with fstat+hash on the same fd (R-FS-2), parent dir
  owner/mode (R-FS-3), exact on-disk name bytes (B4); dash test becomes contract-level + `--` defence in depth

Hostile Git config/helper suppression:
finding (B1/B2) — verified: neutralized clean filter stages un-filtered plaintext (git-crypt secret exposure / LFS blobs);
  attribute filter without driver stages raw; text/eol attributes transform staged bytes despite attributesFile=/dev/null;
  post-index-change hook runs on add unless hooksPath=/dev/null; core.worktree redirects staging. Reject filter/
  transforming attributes, pin autocrlf/safecrlf/add.ignoreErrors, reject fileMode=false, verify staged blob OID ==
  hash(bound bytes). credential/ssh/gpg pins are harmless but not relevant to add. No safe.directory=*.

Process-tree containment:
finding (B5, blocks A11b.2.2 only) — unconditional killpg after leader exit; scoped SIGTERM/SIGHUP/SIGINT handlers;
  explicit decision for parent SIGKILL on macOS (supervisor, or documented recovery-detectable residual accepted by
  security review); assert single-process child tree

Approval boundary preserved:
PASS (R6: primitive never reads ApprovalValidation)

Audit handoff sufficient:
finding (minor) — add action_digest, root dev/inode, object format, post-verify code; typed failure result with
  before/after evidence; reuse Action.expected_state.digest. No public path before durable audit: ACCEPTABLE

Test matrix sufficient:
finding — add R8 cases (encrypting clean filter, driverless filter attr, eol/ident attrs + blob-OID verify,
  post-index-change hook, included config, core.worktree/gitfile/commondir/alternates, index.lock, split/sparse/
  skip-worktree/assume-unchanged/ITA, case/NFD names, stat-only unrelated change, parent dir mode, descendant after
  leader exit, real SIGTERM/SIGHUP, import boundary)

New blockers:
B1 mutation must reject (not neutralize) filter config and any target filter attribute — secret/LFS integrity
B2 reject content-transforming attributes; pin autocrlf/safecrlf/add.ignoreErrors; staged blob OID + mode verification
B3 repository/index layout rules decided in spec (no "unless implementation proves")
B4 post-verify defined as semantic index-entry equality + exact path bytes
B5 (A11b.2.2 only) concrete process-containment mechanism incl. parent-SIGKILL decision

May implement A11b.2.1:
NO — after a documentation-only spec addendum resolving B1–B4 (and recording R5–R10) passes a targeted check;
  A11b.2.2 additionally requires B5. No re-review of the whole spec is needed.
```

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #42 的 branch。spec 和程式碼都沒有修改。scratchpad 中的 disposable repo 只用於驗證，沒有刪除，也沒有加入 Git。
