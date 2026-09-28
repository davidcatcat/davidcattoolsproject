# nlf A5：搜尋深度（`--max-depth` / `--min-depth`）

日期：2026-09-28
分支：`feat/nlf-roadmap-a5-depth-control-20260928`（從 main `0833f52` 建立；**未 merge 到 main**）
版本：維持 0.4.0（roadmap 建議 A3–A5 合併為 0.5.0，由人類決定；沒有 bump、沒有 tag）

## 最終 CLI syntax

```
--max-depth N      --max-depth=N
--min-depth N      --min-depth=N
```

- 採用與 `-d/--dir` 相同的風格：空白分隔與 `=` 兩種寫法都支援；沒有短旗標。
- N 只接受十進位數字，範圍 0–4096，允許前導 0（`007` = 7）。
- 下一個參數即使以 `-` 開頭也會被當成值再驗證（`--max-depth -1` 報錯，而不是把 `-1` 當 flag），和 `--dir` 一致。
- 每個 flag 只能出現一次。
- 沒有新增自然語言語法：`最多 N 個` 仍然是數量上限，不是深度。

錯誤（exit 2，stdout 為空，格式為 `nlf: <msg>` + `Run 'nlf --help' for usage.`）：

| 輸入 | 訊息 |
|------|------|
| 缺值、`""`、`--max-depth=` | `--max-depth needs a number`（`--min-depth` 同理） |
| `-1`、`+1`、`1.5`、`" 1"`、`0x1`、`１`、`1e3`、`4097`、20 位數、`--`、`--explain` | `--max-depth needs a whole number from 0 to 4096, got "-1"` |
| 重複 | `--max-depth can be given only once` |
| min > max | `--min-depth 3 is greater than --max-depth 1, so no file can match` |

驗證順序：解析 → flag 互斥 → `min > max`，全部在 help、version、`--explain` 輸出、`--confirm` 提示與搜尋之前。兩個值不會自動交換。

## Depth 精確定義

與 `find` 相同：

- 搜尋位置本身是 depth 0，直接在裡面的項目是 1，再下一層是 2，依此類推。
- symlink 的搜尋位置（例如 macOS 的 `/tmp`）一樣是 0；目錄樹裡的 symlink 仍然不跟隨（沒有改變）。
- **搜尋位置是一般檔案時**（既有行為，保持不變），它本身就是 depth 0 的候選結果：
  - `--max-depth 0` 會輸出它
  - `--min-depth 1` 不會

  這和 `find <file> -maxdepth 0 -type f` 以及 `find <file> -mindepth 1` 相同。
- `--max-depth 0` 用在目錄上時沒有任何結果（exit 1），和 `find <dir> -maxdepth 0 -type f` 相同。
- `--min-depth 0` 等於沒有限制，不會顯示在 `--explain`。

`Query` 新增 `MinDepth int`、`MaxDepth int`、`HasMaxDepth bool`。zero value 代表沒有限制，所以既有程式與測試裡的 `Query{}` 行為不變。

## Traversal pruning 怎麼做到

`internal/search/search.go`：

- `depthFunc(root)` 在走訪前算一次 root 前綴的分隔符號數。之後每個路徑的深度只需要數一次分隔符號，不需要 Stat、也不需要 `filepath.Rel`。它處理的 root 形式：`.`、`./`、`x/`、`/`、`a//b`、symlink root 加上的結尾分隔符號、檔案 root。
- 在 `WalkDir` callback 裡，深度是第一個檢查：
  - **目錄：** depth >= max 就回傳 `fs.SkipDir`。`WalkDir` 對目錄的第一次呼叫發生在讀取目錄**之前**（Go 文件：「before the directory read is attempted … giving the function a chance to return SkipDir … and avoid the ReadDir entirely」），所以 boundary 那一層的目錄根本不會被 `ReadDir`，更下層的東西不會被列出、開啟或回報。`--max-depth 0` 時連 root 目錄本身都不會被讀取。這個檢查排在 exclude 比對之前。
  - **檔案：** depth < min 就直接略過。這排在副檔名、檔名、`d.Info()`（Stat）與開檔讀內容之前。目錄不受 min 影響，會繼續走訪；排除的目錄在任何深度都照樣 prune。
- 排序、數量上限、未排序時找到 N 個就停止（`SkipAll`）的邏輯都沒有改。

**剪枝的可觀察證明（不是只看結果集合）：**

- `TestMaxDepthDoesNotReadBelow`：在 depth == max 放 `chmod 000` 的目錄，再加一個更深的。搭配內容查詢時，Report 必須是空的（沒有任何權限錯誤）。當 max 加 1 時，同一個目錄**會**被回報，這是 positive control，證明陷阱有效。無法讀取的 root 搭配 `--max-depth 0` 時沒有錯誤、沒有結果。
- `TestMinDepthSkipsBeforeStatAndOpen`：
  - depth 1 放 `chmod 000` 的檔案並做內容查詢，`--min-depth 2` 時沒有任何錯誤（證明沒有開檔）。
  - 0o444 目錄（可列出、不能 Stat 裡面的東西）搭配 `--min-depth 3`，沒有任何錯誤（證明在 `d.Info()` 之前就略過）。`--min-depth 2` 則會回報，作為 positive control。
- `TestDepthPruningCLI`：在 CLI 層確認 stderr 完全為空。
- 以 root 身分執行時權限陷阱無效；測試會 `t.Log` 並繼續執行（不 skip）。GitHub runner 不是 root。

## Flag interaction matrix

全部 9 個 flag（`--explain`、`--confirm`、`-l`、`-0`、`--help`、`--version`、`--dir`、`--max-depth`、`--min-depth`）共有 36 組兩兩組合：19 組是明確 conflict，其餘都允許並有測試。A5 新增 4 個 conflict row，附加在既有 15 列之後，既有列的順序與訊息 byte-identical：

| 組合 | 訊息 |
|------|------|
| `--help` + `--max-depth` | `-h/--help never searches, so --max-depth has no effect; use it without --max-depth` |
| `--help` + `--min-depth` | `-h/--help never searches, so --min-depth has no effect; use it without --min-depth` |
| `--version` + `--max-depth` | `--version never searches, so --max-depth has no effect; use it without --max-depth` |
| `--version` + `--min-depth` | `--version never searches, so --min-depth has no effect; use it without --min-depth` |

允許：深度 flag 可以搭配 `--dir`、`--explain`、`--confirm`、`-l`、`-0`，兩個深度 flag 也可以一起用（`min == max` 允許，代表剛好那一層）；也可以搭配查詢裡的搜尋位置（`go files in <dir>`）。`--help --max-depth -1` 會先回報值的錯誤，因為解析在互斥檢查之前，和 `--dir` 相同。

## `--explain` / `--confirm` parity

- Conditions 新增一列：en `depth:`，zh `搜尋深度`。
  - 形式：`at most M level(s)`、`at least N level(s)`、`N to M levels`、`exactly N level(s)`
  - 中文：`最多 M 層`、`至少 N 層`、`N 到 M 層`、`剛好 N 層`
  - 附註：搜尋位置是第 0 層，直接在裡面的是第 1 層。

  `--confirm` 的計畫一併顯示。
- 近似 find 指令：`-mindepth N` / `-maxdepth N` 放在起點後面、所有其他條件之前（GNU find 對放在後面的 global option 會在 stderr 警告；BSD 不會）。
- **有 exclude 時，最小深度寫成 `-type f -path '<root>/*/*'`，不用 `-mindepth`。**
  - 原因：BSD 與 GNU find 的 `-mindepth` 都不會在較淺的層級套用任何測試，包括 `-prune`，所以較淺的排除目錄仍會被走訪並輸出；nlf 則在任何深度都 prune。
  - BSD 實測：`find r -mindepth 2 -type d \( -iname vendor \) -prune -o -type f -print` 印出了 `r/vendor/v.txt`。
  - `-path` 的 `*` 會比對 `/`，BSD 與 GNU 都一樣。
  - root 的寫法與 find 印出的起點一致：symlink root 的結尾 `/`、`./-x`、`/`，都經過 `globEscape`。
- 沒有深度 flag 時，指令與 A4 byte-identical。
- `--explain` 仍然不搜尋（QA 以 `chmod 000` 的 root 驗證：exit 0、沒有錯誤）。
- `TestExplainParity` 在 macOS（BSD find）與 Linux（GNU find）實際執行印出的指令。depth 案例涵蓋：
  - `--max-depth 0`（空結果）、`--max-depth 1`、`--max-depth 2` 搭配 exclude、內容、`--min-depth 2/3`（含 exclude）、min == max、大小
  - 排序：largest／newest 搭配深度
  - 需要 root guard 的 root（`real/vendor`、`real/vendor/`、`sym/vendor`）
  - 檔案 root

  **find 的 stderr 必須是空的**（所有案例都適用，包括既有案例），用來抓 GNU 的 global option 警告。

## Backward compatibility

- 沒有使用深度 flag 時，行為完全不變。QA 用 HEAD 與新版 binary 比對了 300 組：15 個查詢 × 無 flag／`-l`／`-0`／`--explain` × 5 種 root（含 symlink、結尾 `/`、檔案、不存在的目錄）。stdout、stderr、exit code 全部 byte-identical。
- 唯一的輸出差異是 `--help`：新增一行 `--max-depth N … --min-depth N …`，並把 `-h, --help` 與 `--version` 合併成一行。help 仍是 70 行、≤ 100 欄。
- parser、副檔名、檔名、內容、時間、大小、exclude、排序的語義都沒有改。`internal/query` 的 parser 檔案與 workflow 都沒有修改。
- 以前 `--max-depth` 是 unknown flag（exit 2），所以不會有既有用法被改變。

## 測試

**先寫測試：** 在只加了欄位、沒有實作的 stub 上，新測試因正確的原因失敗（unknown flag、沒有剪枝或過濾、缺少 Conditions 列或 help 文字）。只有 empty tree 的案例本來就會通過。

新增的測試：

- **`internal/search/depth_test.go`：**
  - `TestDepthFunc`（17 種 root／path 形式）
  - `TestDepthFuncMatchesWalk`（實際走訪 9 種 root 寫法）
  - `TestMaxDepth`（0–5，zero Query）
  - `TestMinDepth`、`TestMinMaxDepth`（含 min == max）
  - `TestDepthEmptyTree`、`TestDepthRootFile`、`TestDepthSymlinkRoot`
  - `TestDepthExcludeAboveMinDepth`（排除目錄在 boundary 上、以及比 min 淺）
  - `TestDepthUnicodeSpacePath`
  - `TestDepthSortAndLimit`、`TestDepthUnsortedLimit`
  - 兩個剪枝陷阱測試
- **`internal/query/depth_test.go`：** `TestDepthConditions`（4 種形式 × 中英、單複數、位置）；`TestFindCommand` 新增 10 列。
- **`tools/nlf/depth_test.go`：** `TestDepthFlagParsing`、`TestDepthFlagValidation`（所有錯誤輸入的精確訊息，以及 `--explain`／`--confirm` 在錯誤時不輸出）、`TestDepthEndToEnd`（`-l`、`-0`、`--confirm`、檔案 root、查詢內的搜尋位置）、`TestDepthPruningCLI`。
- **既有測試只加強：**
  - `TestFlagConflicts`、`TestFlagConflictMatrixCLI`：9 個 flag、36 組
  - `TestHelpContent`、`TestDocsMatchBehavior`：doc-example regex 納入深度 flag，README 的深度範例會被實際執行
  - `TestConfirmPlan`
  - `TestExplainParity`：既有案例都保持 `mustFind=true`，並新增 stderr 為空的要求
- **`scripts/ci-smoke.sh`：** 7 個深度檢查，以及一個 `--explain` parity 步驟（實際用 `sh -c` 執行 `-path` 形式的指令，比對 nlf 的結果）。

**Mutation 驗證：** Implementation agent 做了 14 個、QA 做了 26 個，全部被測試抓到。使用者指定的 5 個都在其中：

- 拿掉 max-depth 剪枝（改成走完再過濾）
- min-depth off-by-one
- max-depth off-by-one
- 拿掉 min／max 驗證
- 深度過濾移到開檔之後（以及移到 `d.Info()` 之後）

其他 mutant：

- min 套用到目錄
- 忽略 `HasMaxDepth`
- `depthFunc` 少了 `.` 或 `/` 特例
- 拿掉位數檢查、4096 上限、空值檢查、重複檢查
- 拿掉 conflict 列
- `-maxdepth` 放到 `-type f` 後面
- 有 exclude 時仍用 `-mindepth`
- 拿掉 Conditions 列或單複數錯誤

## macOS / Linux 測試結果

- **macOS（Lead，Go 1.27.1，BSD find）：** 每一項都以 exit code 確認為 0。

  | 步驟 | 結果 |
  |------|------|
  | gofmt | 0 |
  | `make check` | 0 |
  | `go test -race ./...` | 0 |
  | `go test -count=10 -shuffle=on ./...` | 0 |
  | `make dist` | 0 |
  | `scripts/ci-smoke.sh`（bash 5 與 `/bin/bash` 3.2） | 0 |
  | `go test -v` | 1001 個 PASS、0 個 SKIP |
- **QA：** 用 BSD `/usr/bin/find` 做了約 900 組 nlf 與 `--explain` 指令的比對，涵蓋所有深度值、root 形式（`.`、`./`、`..`、`/`、`//`、`r//`、symlink 有沒有結尾 `/`、檔案、空目錄、Unicode）與查詢類型，結果集合全部一致。唯一的例外是既有的「排序 pipeline 以行為單位，檔名含換行時會拆開」限制，README 早已記載。
  - 3000 個目錄的 fan-out 搭配未排序 `first 3`，約 0.01 秒就停止，放在後面的權限陷阱沒有被碰到。
- **Review：** APPROVE；兩個 NIT 已修正（測試變數名稱遮蔽 builtin；smoke 的 `-path` 檢查改得更精確，並檢查 nlf 搜尋的 exit code）。
- **Linux：** 本機沒有 GNU find，Docker daemon 也沒有啟動，所以 Linux 的實際結果由 CI 的 ubuntu-latest job 驗證（`TestExplainParity` 用 GNU find、`ci-smoke.sh` 用 Linux binary）。

## CI run ID

A5 branch push 之後會觸發 GitHub Actions。Lead 無法讀取 private repo 的 Actions 結果（沒有 `gh`，也不讀 keychain），**run ID 與 ubuntu-latest／macos-latest 的結果需要人類確認後補上**。

## 未解問題與風險

- **GNU 的 stderr 檢查也套用到既有案例。** 既有的 root guard 案例（`real/vendor/`、`sym/vendor`）會產生以 `/` 結尾的 `! -path 'real/vendor/'`。GNU findutils 對以 `/` 結尾的 `-path` 會警告，但 pattern 等於起點時例外（`matches_start_point`）。QA 對此的信心約 70%。
  - 如果 ubuntu CI 出現這個警告，guard 的行為要保留（GNU 印出的起點帶結尾 `/`，拿掉會 prune root）。可以改寫成 `-path 'X/vendor[/]'`，或把 stderr 檢查限縮到 depth 案例並記錄。
- **`--max-depth 0` 搭配無法讀取的目錄 root：** nlf 不開啟它（exit 1，只有「沒有找到」），BSD find 仍會回報 Permission denied。這已文件化。
- **深度上限 4096，而 find 接受任意整數。** 任何可開啟的路徑都不會更深；這個上限也限制了 `-path` 呈現的長度。
- 未排序的 `top N` 搭配深度時，選到的檔案可能和 `find … | head` 不同（沿用既有的 O3，已文件化）。
- 0o444 陷阱依賴檔案系統提供 `d_type`（ext4、overlayfs、APFS 都有）。在不提供 `d_type` 的檔案系統上，`ReadDir` 本身就會失敗，測試會報錯，而不是誤判通過。
- `at most 0 levels` 的英文用詞略顯生硬，但語意正確（QA NIT，保留）。

## 範圍確認

- **沒有做 A6**（top-N heap）：排序仍是收集後排序再截斷，`sortResults` 沒有修改。
- 產品原則不變：
  - deterministic、read-only
  - runtime 不用 LLM、不連網路
  - production code 不執行 shell 或外部程式（`TestProductionCodeIsReadOnly` 通過），不用 sudo，沒有新增依賴
  - Go-native 走訪，macOS／Linux 都是一等平台
  - 看不懂或矛盾的輸入 exit 2，不默默擴大、縮小或修正查詢
  - `--explain` 不搜尋
  - exit 0／1／2 的語義不變
