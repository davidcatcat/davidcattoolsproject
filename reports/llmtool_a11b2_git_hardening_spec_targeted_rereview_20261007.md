# llmtool A11b.2 Git Hardening spec — targeted re-review（2026-10-07）

## 範圍與方法

- **審查對象：**
  - PR #42，**exact remediated spec head `29d0b6aba1e41f301f3ad15520afbe87c496ddec`**；
  - 目前的 PR head 是 `ec99aa3`。`29d0b6a..ec99aa3` 之間只新增了 re-review handoff，**spec 沒有改變**；
  - PR 中沒有任何 `reports/` 以外的檔案。
- **修正的內容：** 相對於原本 review 的 head `2fc0f94`，有兩個修正 commit：
  - `b111291`：resolve blockers；
  - `29d0b6a`：align test matrix。

  spec 的變更是 +138／−35，並新增了 §20「Security-review addendum — normative resolutions」。依照 spec 的規定，**addendum 在發生衝突時具有最終效力**。
- **方式：**
  - 逐行閱讀 `2fc0f94..29d0b6a` 的 spec diff，以及修正後的 §4 API；
  - 只針對 B1–B4、R5–R10、B5 的狀態和 scope 進行確認；
  - 原本已經 PASS 的部分，只檢查修正是否造成矛盾。
- **沒有做的事：** 沒有修改 spec 或程式碼，沒有呼叫 company 或 Codex，沒有 merge。

## 結論：PASS

B1–B4 都已經在 spec 中做出**明確的規範性決定**，不再交給實作時判斷。R5–R10 都已經寫入。B5 被正確地限定在 A11b.2.2 的 gate 中。下方有 2 個小的文字不一致，不構成 blocker，在實作時依照下方的解讀處理即可。

## B1：CLOSED

- §2 的 A5 清單改為：*"for mutation, reject any configured filter driver instead of neutralizing it"*。
- §9 寫明：*"**reject, do not neutralize**"*。也就是說：
  - 任何有效 config 中定義了 `filter.<driver>.(clean|smudge|process|required)` → reject；
  - 任何 target 的 `filter` attribute 被設定為 driver 或任何值，**即使那個 driver 沒有 config** → reject。
- 原本的「neutralized exactly as A5 does」和四行 `filter.*=` 都已經刪除。
- §20 B1 明確寫出這是為了防止 git-crypt 的明文或完整的 LFS 內容被 staged。原本 review 中實際驗證的 1 和 1b 都已經被涵蓋。
- **A5 的唯讀中和沒有被重新定義：** 修改只出現在「for mutation」的條件下，`git_intel.py` 不在修改範圍內。

## B2：CLOSED

- target 使用固定的 hardened `git check-attr -z` 查詢。`filter`、`text`、`eol`、`crlf`、`ident`、`working-tree-encoding` 只要**不是 `unspecified` 或 `unset`**，就 reject。所以 `-text` 和 `binary` macro 可以通過，`text=auto` 和 `eol=lf` 都會被拒絕。
- 釘住 `core.autocrlf=false`、`core.safecrlf=false`、`add.ignoreErrors=false`；**有效值為 `core.fileMode=false` 時 reject**。
- **post-verify：** *"compute the Git blob OID from the **A11b.1-bound original bytes** using the repository **object format** and require the staged OID to match; staged mode (100644/100755) must match the bound execute bit"*。
  - 這比比對 worktree 的 SHA 或 raw index 的 SHA **更強**：直接證明 index 中的內容就是被綁定的 bytes。
  - 修正後的 API 中有 `object_format` 欄位，可以支援 sha1 和 sha256 兩種 repo。

## B3：CLOSED

**所有項目都是硬性決定：**

- §5 第 9、10 項，§8 的「Normative index subset」，以及 §20 B3：
  - 拒絕 linked worktree、gitfile、alternate git dir、`commondir`、object alternates、`core.worktree`、**config 的 `include`／`includeIf`**、split／sparse index、alternate index、sparse checkout；
  - 只支援「真正的 `<root>/.git` 目錄，加上單一的 `<root>/.git/index`」；
  - **每一個 Git subprocess 都使用程式產生的絕對路徑 `GIT_DIR=<root>/.git` 和 `GIT_WORK_TREE=<root>`**；
  - 拒絕 `core.bare=true` 和 bare repo。
- **index：**
  - 用 no-follow 開啟，必須是一般檔案而且 nlink=1；
  - 由**唯一的 A11b.2.1 函式**計算 SHA-256，將來產生 Action 的程式也使用同一個函式；
  - **缺少 index → reject**；
  - **`index.lock` 存在 → reject，而且絕對不刪除**；
  - split／shared index 的判斷依據：`core.splitIndex`、`sharedindex.*` 或 `link` extension；
  - sparse 的判斷依據：`index.sparse`、`core.sparseCheckout`、`info/sparse-checkout` 或 `sdir` extension；
  - **任何地方有 skip-worktree、assume-unchanged 或 intent-to-add → reject**。
- §6 加入：**mutation 的 preflight 關閉 rename 偵測**。target 已經 staged、ITA、assume-unchanged、skip-worktree、rename／copy、deletion、ignored-untracked、sparse、gitlink／submodule → reject。
- **root 和 path 的 ownership：**
  - root 由 euid 擁有（§5 第 5 項）；
  - UID 0 → reject（§5 第 6 項）；
  - root 本身是 symlink → reject（§5 第 7 項）；
  - canonical root 必須和 discovered root canonicalize 之後的結果完全相等（§5 第 2 項）。也就是說，canonical root 中只要有 symlink 的 component，就不可能相等，**等同於 `canonical_root == realpath(root)`**；
  - **從已經開啟的 root dir-fd 開始，用 `O_DIRECTORY|O_NOFOLLOW` 逐層走訪上層目錄；每一層都必須屬於 euid，而且沒有 group 或 world 的寫入權限**；
  - target 用 `O_NOFOLLOW` 開啟，**在同一個 fd 上做 fstat 和 hash**。
- **沒有剩下會讓 A11b.2.1 不安全的模糊之處。** 有一個實作上的注意事項（見 N2）。

## B4：CLOSED

- §8 的 post-verify 改為用 `(exact path bytes, mode, object OID, stage, skip-worktree／intent-to-add flags)` 比較 stage-0 entry。
- **預期的 post 狀態 = 完整的 pre entry 集合，只換掉被授權的 target**。無關的語意 entry 必須完全相同，**不能出現新的 path**。
- **raw index 的 SHA 只作為 race 和 accounting 的證據，不作為語意的證明**，並且明確說明了理由（Git 會更新 stat 和 cache bytes）。所以 stat-only 的更新不會造成誤判；matrix 中也有「unrelated stat/cache refresh … still PASS」這個 test。
- **path bytes 必須完全相同；大小寫或 Unicode 正規化的替換必須 FAIL**。§7 也要求 directory entry 的名稱 bytes 必須相同，所以在 preflight 階段就會拒絕 alias。
- target 的 blob OID 和 mode 都另外驗證（B2）。
- matrix 中有「concurrent unrelated OID/path/mode mutation causes post-verify failure」這個 test。

## R5–R10：CLOSED

| 項目 | 確認 |
|---|---|
| R5 | test matrix 中有：*"import/AST boundary: outside tests, no production CLI/entrypoint/chat/orchestration/benchmark module imports or exposes the mutation module"*。§1 和 §13 規定在授權和 durable audit 出現之前，不能有 public path |
| R6 | matrix 中有「primitive API does not accept/read `ApprovalValidationResult`」；§20 寫明授權是外部呼叫端的前提條件。§14 原本的條款（`bindings_valid` 不等於授權、`source=USER` 不等於身分驗證、沒有 debug bypass）都保留 |
| R7 | `GitAddPreflight` 加入了 `action_digest`、`root_device`、`root_inode`、`object_format`，以及原本就有的 root、head、`index_sha256`、paths、`state_digest`。`GitAddResult` 加入了 `post_verify_code`。新增了 typed 的 **`GitAddFailure`**（包含 before、after_index_sha256、observed_staged_paths、failure_code、post_verify_code）。§20 R7 規定 `state_digest` 必須是 `Action.expected_state.digest`。這些欄位都不需要 raw stderr 或檔案內容 |
| R8 | 本輪要求的 20 個 adversarial case 都可以找到：encrypting clean filter、driverless `filter=lfs`、text／eol／ident／working-tree-encoding、`post-index-change`、included config 加上 `core.worktree`、`core.fileMode=false`、index.lock、missing index、split／shared／sparse、skip-worktree／assume-unchanged／ITA、case／NFC／NFD、stat-only refresh、unrelated OID 改變、parent dir 的 owner／mode、import boundary。gitfile、commondir、alternates 列在 §20 R8 的規範清單中（見 N3） |
| R9 | §20 R9：*"a malicious same-UID concurrent process is outside the preventable isolation boundary; pre/post verification detects accidental/residual races but is not an OS sandbox"*。這個邊界可以接受，而且**沒有被用來豁免一般可以偵測的 race**：兩階段的驗證、同一個 fd 的 fstat 和 hash、語意的 post-verify 都仍然是必要條件 |
| R10 | §20 R10：只有在 **test 證明**所有 repo-code 的執行途徑（filters、轉換 attribute、hooks、fsmonitor、helpers、alternate worktree）都被拒絕或停用時，ADD 子集才可以不使用 OS sandbox。我逐項對照 `git add` 可能的執行途徑：filter（拒絕）、hooks 包括 `post-index-change`（hooksPath=/dev/null，並且有 test）、fsmonitor（釘住）、include 和 `core.worktree`（拒絕）、textconv 和 external diff（add 不會使用）、credential、ssh、gpg、protocol（add 不會使用，並且已經釘住或設為 never）、`trace2`（只從 system 或 global config 讀取，已經被 NOSYSTEM 和 GLOBAL 排除）。**沒有發現剩下由 repository 控制的可執行途徑** |

## B5：DEFERRED TO A11b.2.2

§20 B5 明確寫出：「A11b.2.1 may implement deterministic preflight before B5 is closed」。在 A11b.2.2 之前，必須經過 targeted security review，核准四件事：

1. 即使 leader 已經結束，也一律執行 process-group cleanup；
2. scoped 的 SIGTERM／SIGHUP／SIGINT handler，經過 cleanup 之後恢復原本的 handler；
3. macOS 上 parent 被 SIGKILL 的策略：supervisor 或 parent-death 的等效機制，或者明確接受殘餘風險，並透過 index 的語意 post-state 偵測；
4. 子 process tree 只有 Git 的前提。

matrix 中也已經加入「leader exits while descendant remains」和「real SIGTERM/SIGHUP/KeyboardInterrupt」的 test。**這個 gate 是正確的，不阻擋 A11b.2.1。**

## Scope 和 public path：PASS

- §1 的非目標清單（commit、apply、checkout、switch、reset、clean、branch／tag、merge、rebase、cherry-pick、fetch、pull、push、remote、submodule、任意 argv、shell、public CLI、自動執行、approval issuer、retry、fallback、repair、A11c）**都沒有被修改**。
- 修正的內容只有收緊和新增 reject，**沒有擴大 scope**，也沒有新增 public 入口。

## 非 blocker 的注意事項（A11b.2.1 實作時依照以下解讀）

- **N1（文字不一致）：** test matrix 的第 564 行仍然寫著「target already staged unexpectedly → reject **unless exact expected state explicitly models** and tests that case」，但 §6 第 226 行和 §20 都寫成無條件 reject。
  - A11b.1 的 `FileExpectation` **無法表示 index 中的 staged 狀態**，所以「explicitly models」實際上不可能成立。
  - 實作時**一律 reject**。可以在之後修正 spec 時，順便把第 564 行改成一致的文字。
- **N2（實作細節）：** 偵測 `include`／`includeIf` 時，必須用**不跟隨 include** 的方式讀取 local config（例如 `git config --local --no-includes --name-only --get-regexp '^include(if)?\.'`），才能看到 include 指令本身。
  - 如果 repo 啟用了 `extensions.worktreeConfig`，`config.worktree` 也是一個 config 來源。建議一併 reject `extensions.worktreeConfig`，或者確認偵測的範圍包含它。
  - 這只是實作細節，不影響規範。
- **N3（test 的落點）：** gitfile、commondir、alternates 列在 §20 R8 中，但第 16 節的 matrix 沒有逐條列出。依照 addendum 的效力，A11b.2.1 的 test 必須逐一涵蓋這三項。

## 判定

```text
A11b.2 TARGETED SPEC RE-REVIEW:
PASS

Exact review head:
29d0b6aba1e41f301f3ad15520afbe87c496ddec

B1 filter mutation rule:
CLOSED — configured clean/smudge/process/required filter and any target filter attribute (even driverless) → REJECT;
  neutralize-and-continue removed; A5 read-only neutralization untouched

B2 attributes/blob verification:
CLOSED — filter/text/eol/crlf/ident/working-tree-encoding rejected unless unspecified/unset; autocrlf/safecrlf/
  add.ignoreErrors pinned; core.fileMode=false rejected; staged blob OID == object-format hash of bound bytes and
  staged mode == bound execute bit

B3 repository/index subset:
CLOSED — hard decisions: real root/.git, no gitfile/commondir/alternates/core.worktree/include/includeIf/linked
  worktree/alternate GIT_DIR or index/split/sparse; explicit absolute GIT_DIR/GIT_WORK_TREE; missing index and
  index.lock → reject (never delete); skip-worktree/assume-unchanged/ITA → reject; renames off; root owned by euid,
  UID 0 rejected, dir-fd O_NOFOLLOW parent walk with euid-owned, non group/world-writable parents

B4 semantic post-verify:
CLOSED — (path bytes, mode, OID, stage, flags) entry-set equality = pre-set with only targets replaced; no new path;
  stat refresh tolerated; exact name bytes; case/NFC/NFD alias fails; raw index SHA kept as evidence only

R5–R10:
CLOSED

B5 status:
DEFERRED TO A11b.2.2 — explicit four-point gate recorded; A11b.2.1 not blocked

Scope ADD-only:
PASS

No public mutation path:
PASS

New A11b.2.1 blockers:
NONE (non-blocking: N1 treat line-564 "already staged" as unconditional reject; N2 detect include/includeIf with
  --no-includes and reject or cover extensions.worktreeConfig; N3 add explicit gitfile/commondir/alternates tests)

May implement A11b.2.1:
YES
```

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #42 的 branch。沒有修改 spec 或程式碼，沒有 merge，也沒有開始 A11b.2.2 或 A11b.3。
