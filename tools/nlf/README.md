# nlf — Natural Language Find

用中文或英文描述你要找的檔案，不用記 `find`、`grep`、`awk` 的語法。

```bash
nlf "找出最近 7 天修改、大於 1MB 的 go 檔，排除 vendor，最多 10 個"
nlf "python files containing \"import os\" modified within 3 days, top 5"
nlf --explain "最大的 5 個圖片"          # 只顯示解析結果與近似指令，不搜尋
nlf --confirm "go files larger than 1MB"   # 先顯示執行計畫，輸入 y 才搜尋
```

- 單一 binary，macOS 優先，只用 Go 標準函式庫
- 不呼叫任何 LLM API；解析規則是固定的（deterministic），相同輸入永遠得到相同結果
- 唯讀：不會修改、建立、刪除或移動任何檔案，不執行 shell 或其他程式，也不會自動使用 `sudo`
- 看不懂的詞會直接報錯，不會默默忽略而擴大搜尋範圍

## Installation

nlf 是單一、靜態連結的執行檔（`CGO_ENABLED=0`，只用 Go 標準函式庫）。執行時不需要 Go、不需要任何函式庫，也不需要網路。只有 build 的機器需要 Go 1.23 以上。

### macOS

從原始碼 build（需要 Go：`brew install go`）：

```bash
cd tools/nlf
make build                                   # 產生 bin/nlf（本機架構）
make check                                   # gofmt 檢查 + go vet + go test + build
sudo install -m 755 bin/nlf /usr/local/bin/nlf
nlf --version
```

- `make build` 會依照 build 機器的架構產生執行檔。Apple Silicon（`uname -m` 顯示 `arm64`）和 Intel（顯示 `x86_64`）各自 build 即可。
- 要給另一種架構的 Mac 使用時，執行 `make dist`，再挑選 `bin/nlf-<版本>-darwin-arm64` 或 `bin/nlf-<版本>-darwin-amd64`。
- 如果不想用 sudo，也可以安裝到自己的目錄，例如 `install -m 755 bin/nlf ~/bin/nlf`，並確認 `~/bin` 在 `PATH` 裡。
- 透過瀏覽器或 AirDrop 取得的執行檔，可能帶有 quarantine 屬性，Gatekeeper 會擋下。確認來源可信之後，可以用 `xattr -d com.apple.quarantine ./nlf` 移除。自己 build 的執行檔不會有這個問題。

### Linux

目標機器不需要安裝 Go。建議在有 Go 的機器上 cross-compile，再把執行檔帶進去。

**1. 在有 Go 的機器上 build（macOS 或 Linux 都可以）**

```bash
cd tools/nlf
make dist
# 產生：
#   bin/nlf-<版本>-linux-amd64    x86_64 伺服器
#   bin/nlf-<版本>-linux-arm64    ARM64 伺服器（例如 AWS Graviton、Raspberry Pi 64-bit）
#   bin/nlf-<版本>-darwin-amd64 / -darwin-arm64
#   bin/SHA256SUMS
```

只需要單一平台時，可以直接 build：

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o nlf-linux-amd64 .
```

**2. 確認目標機器的架構**

```bash
uname -m      # x86_64 → linux-amd64；aarch64 → linux-arm64
```

**3. 把執行檔帶進私有網路**

只需要兩個檔案：執行檔本身和 `SHA256SUMS`。依環境選擇一種方式：

```bash
# 可以透過 jump host（bastion）連線時
scp -J user@bastion bin/nlf-0.1.0-linux-amd64 bin/SHA256SUMS user@target:~/

# 只能用實體媒體（USB）或內部檔案伺服器 / 套件庫時，
# 把同樣兩個檔案放上去，再從目標機器取回
```

**4. 在目標機器上驗證 checksum**

```bash
sha256sum -c --ignore-missing SHA256SUMS   # 必須顯示 nlf-...-linux-amd64: OK
```

**5. 安裝**

```bash
# 系統層級（需要 sudo）
sudo install -m 755 nlf-0.1.0-linux-amd64 /usr/local/bin/nlf

# 不需要 sudo：安裝到自己的目錄
mkdir -p ~/.local/bin
install -m 755 nlf-0.1.0-linux-amd64 ~/.local/bin/nlf
export PATH="$HOME/.local/bin:$PATH"       # 需要的話，加到 ~/.bashrc
```

`/tmp` 或家目錄如果掛載成 `noexec`，放在那裡的執行檔無法執行。請改放到可以執行的目錄，例如 `/usr/local/bin` 或 `~/.local/bin`。

**6. 確認可以使用**

```bash
nlf --version
nlf --explain "找 log 檔"                                  # 只解析，不搜尋
nlf --dir /var/log "找最近 1 天修改的 log 檔"
nlf --dir /etc "find conf files"
nlf --dir /opt/app '包含 "Connection refused" 的 log 檔'
nlf --confirm --dir /home "排除 .cache、node_modules 目錄，找最大的 10 個檔案"
```

**Linux 使用注意事項**

- 搜尋 `/` 時，請排除虛擬檔案系統，或把範圍縮小：`nlf --dir / "exclude /proc and /sys, larger than 100MB"`。含 `/` 的排除路徑是相對於搜尋位置解析的，所以只會排除 `/proc` 本身，不會排除其他名叫 proc 的目錄。
- 常見的 Linux 檔案系統（ext4、xfs）區分大小寫。排除路徑依檔案系統判斷，大小寫必須寫對；排除「名稱」（不含 `/`）仍然不分大小寫。
- 沒有權限的目錄會略過並列在 stderr。nlf 不會自動使用 sudo。在 Linux 上，權限提示會建議用 `ls -ld` 檢查擁有者與權限、改用有權限的帳號執行，並提醒 SELinux / AppArmor 也可能拒絕存取；macOS 才會提到「完整磁碟取用權限」。
- 在 Linux 上，`--explain` 與 `--confirm` 顯示的是 GNU/Linux 語法的近似指令（標示為「近似的 GNU/Linux 指令」）。和 macOS 版唯一的差別是排序用的 stat：GNU coreutils 用 `stat -c '%s %n'` / `stat -c '%Y %n'`，macOS 用 `stat -f '%z %N'` / `stat -f '%m %N'`；find、grep、sort、cut、head 的部分兩邊相同。nlf 本身不會執行這些指令。
- 目前在 macOS 上執行完整測試；Linux 的部分已確認可以 cross-compile、產出的是靜態 ELF，測試也能在 `GOOS=linux` 下編譯，但還沒有在 Linux 主機上實際跑過測試（見 Known limitations）。

### 解除安裝

```bash
sudo rm /usr/local/bin/nlf       # 或 rm ~/.local/bin/nlf
```

nlf 不會建立設定檔、快取或其他檔案，所以沒有其他東西需要清理。

## Usage

```text
nlf [flags] "<query>"

--explain        顯示 nlf 怎麼理解查詢，以及近似的 find 指令；不實際搜尋
--confirm        先顯示執行計畫（條件、近似指令、安全性），輸入 y 或 Y 才搜尋
-l, --long       每行前面加上大小與修改時間
-d, --dir PATH   搜尋位置（預設為目前目錄）
-h, --help       說明與範例
--version        版本
```

輸出為每行一個路徑，可以直接接 pipe（例如 `| xargs open`）。
提示訊息都寫到 stderr，語言會跟著查詢語言（有中文字就用中文）。

Exit status：`0` 有找到，或 `--confirm` 被使用者取消；`1` 查詢有效但沒找到；`2` 錯誤（看不懂查詢、flag 錯誤、目錄不存在、根目錄沒有權限）。

### 常用範例

```bash
nlf "找 ovpn 檔"
nlf --dir ~/Downloads "找 ovpn 檔"
nlf '找內容包含 "20.2.250.249" 的檔案'   # 要找的確切內容請加引號
nlf "找最近 7 天修改的 go 檔"
nlf "找最大的 5 個檔案"
nlf "排除 vendor 和 node_modules，找 go 檔"
nlf --confirm "找 ovpn 檔"   # 先看執行計畫與安全性摘要，輸入 y 才搜尋
nlf --explain "找 ovpn 檔"   # 只看 nlf 怎麼理解查詢，不搜尋
```

### `--explain` 與 `--confirm`

| | `--explain` | `--confirm` |
|---|---|---|
| 顯示解析結果與近似 find 指令 | 是（stdout） | 是（stderr，連同安全性摘要） |
| 詢問 | 不詢問 | `Execute this search? [y/N]:` |
| 搜尋 | 永遠不搜尋 | 只有輸入 `y` 或 `Y` 才搜尋 |

- `--confirm` 只接受一整行剛好是 `y` 或 `Y`；`n`、直接按 Enter、`yes`、其他文字、EOF 一律取消，顯示 `Cancelled.`，exit 0。只讀第一行。
- 執行計畫與提示都寫到 stderr，所以 stdout 仍然只有結果路徑，可以接 pipe。
- 安全性摘要（Read / Modify / Create / Delete / Execute shell / sudo、Risk）描述 nlf 自己的搜尋：目前一律是 `READ-ONLY`（只讀取目錄與檔案）。這個宣告由測試檢查原始碼維持（不得使用 `os/exec` 或任何會建立、修改、刪除檔案的 `os` 函式）。
- 計畫裡的指令與 `--explain` 一樣只是近似、供理解用，並依作業系統顯示 macOS 或 GNU/Linux 語法；nlf 不會執行它。
- `--explain` 與 `--confirm` 不能同時使用（exit 2）。

### 支援的條件

所有條件以 AND 組合；多個副檔名之間是「任一符合」。

| 條件 | 範例 |
|------|------|
| 副檔名 | `.go`、`*.md`、`go files`、`pdf 檔`、`go 或 md 檔`、`圖片`、`影片`、`images`、`副檔名是 heic`、`找 ovpn 檔`、`find pem files` |
| 內容 | `containing "TODO"`、`with TODO`、`包含 TODO`、`內容有「錯誤訊息」`、`ignore case`、`不分大小寫` |
| 修改時間 | `last 7 days`、`in the past 3 hours`、`older than 30 days`、`30 days ago`、`today`、`yesterday`、`最近 7 天`、`3 小時內`、`超過 30 天`、`3 天前`、`今天`、`昨天` |
| 檔案大小 | `larger than 10MB`、`smaller than 1KB`、`at least 1GB`、`> 2gb`、`大於 10MB`、`10MB 以上`、`小於 1KB`、`最大 10MB` |
| 數量 | `top 10`、`first 5`、`最多 10 個`、`前 10 個`、`只要十個` |
| 最大 / 最新 N 個 | `10 largest files`、`最大的 5 個檔案`、`前 3 大`、`newest 3`、`最新的 3 個`、`最近修改的 5 個檔案` |
| 排除目錄 | `exclude node_modules and .git`、`排除 vendor、build 目錄`、`除了 dist 以外`、`except dist dir`、`排除 src/generated`、`排除 ./src/generated` |
| 搜尋位置 | `in ~/Documents`、`under /tmp`、`在 ./src`，或 `--dir` |

`--explain` 範例（在 macOS 上執行；在 Linux 上標題會是「近似的 GNU/Linux 指令」）：

```text
$ nlf --explain "找出最近 7 天修改、大於 1MB 的 go 檔，排除 vendor，最多 10 個"
解析結果：
  搜尋位置       .
  副檔名         .go
  修改時間       晚於 2026-09-20 22:39:05
  檔案大小       > 1 MB
  排除目錄       vendor
  數量上限       10

近似的 macOS 指令（僅供參考，nlf 不會執行它）：
  find . -type d \( -iname vendor \) -prune -o -type f -iname '*.go' -size +1048576c -newermt '2026-09-20 22:39:05' -print | head -n 10
```

## Behavior details

- 大小用二進位單位：1 KB = 1024 bytes，`m` / `mb` 都是 MB（不是分鐘；分鐘請寫 `min` 或 `分鐘`）。
- 「月」= 30 天，「年」= 365 天；「7 天」是從現在往前推 7×24 小時。「今天」「昨天」以本地時間午夜為界。
- 「3 天前」「30 days ago」表示「修改時間早於 3 天前」。
- 內容搜尋是逐字比對（不是正規表示式），預設區分大小寫；前 64 KB 含 NUL 位元組的檔案視為二進位並略過（類似 `grep -I`；macOS 的 grep 會檢查更多內容，NUL 出現在 64 KB 之後的檔案結果可能不同）。
- 副檔名比對不分大小寫。任何英數字詞後面接「檔／檔案／files」等名詞就視為副檔名（`找 ovpn 檔` = `*.ovpn`）；沒有名詞時只認得內建別名（`go`、`python`、`圖片`…）或 `.ext`、`*.ext`、`副檔名 xxx`。常見英文形容詞（`large files`、`old files`）會報錯，不會被當成副檔名。
- 排除目錄以名稱比對（不分大小寫）。含 `/` 時是相對於搜尋位置的路徑：`src/gen`、`./src/gen`、`src/gen/`、搜尋位置底下的絕對路徑或 `~/…` 都可以；路徑在搜尋位置之外會報錯。路徑的大小寫依檔案系統判斷：大小寫不同的寫法，只有在檔案系統把它解析成同一個目錄時（例如 macOS 預設的 APFS）才會排除。
- 只能排除目錄。`排除 .go`、`exclude *.log`、`exclude go files` 這類看起來是檔案類型的寫法會報錯；如果真的是目錄名稱，請加「目錄」／`dir` 或引號。
- `exclude`、`excluding`、`排除`、`skip`、`跳過`、`ignore`、`忽略` 直接表示排除目錄；`except`、`除了`、`不要` 後面要有「目錄／以外／dir」等字才算排除，否則報錯。`without`、`不包括`、`不包含` 是否定條件，v0.1 會報錯。
- 只回傳一般檔案，不回傳目錄；不跟隨目錄內的 symlink（搜尋位置本身是 symlink 時會跟隨，例如 `/tmp`）。
- 隱藏檔與 `.git` 會被搜尋，需要時請加 `排除 .git`。
- 沒有排序時依名稱順序輸出，達到數量上限就停止；有「最大/最新」時會先走完整棵樹再排序。
- `--explain` 顯示的 find 指令是近似的，僅供參考：未排序時的輸出順序（nlf 依名稱順序，find 依目錄讀取順序）、同分時的排序、binary 判斷與大小寫比對可能不同。nlf 本身用 Go 走訪檔案，不會執行任何 shell 指令。
- 沒有權限讀取的目錄會略過；檔案只有在需要讀取內容（內容搜尋）時才會因權限不足而略過，否則仍會列出（與 `find` 相同）。略過的位置在結束時列在 stderr；nlf 不會提權。

## Known limitations (v0.1)

- 不支援條件之間的 OR、否定（「不包含 X」「without X」）、依檔名搜尋（例如 `.DS_Store`）、正規表示式、絕對日期（「2026-01-01 之後」）。
- 未加引號的搜尋文字會一直延伸到空白或標點（`包含 API連線失敗`）。如果後面緊接著 nlf 認得的詞（例如 `包含 設定檔`，「檔」是關鍵字），因為無法確定範圍，會報錯要求加引號；只有「的」「且」「和」這類連接詞可以直接接在後面（`包含TODO且大於1MB`）。空字串 `""` 會報錯。
- 未加引號的路徑會一直延伸到空白或標點，可以包含中文（`在 ~/文件 找 pdf 檔`）。路徑後面請加空白，不要直接接其他字；路徑中有空白時要加引號，例如 `在 "~/My Docs"`。
- 數量上限最多 2147483647；時間最多約 292 年；超過會報錯。
- 主要在 macOS 上開發與測試。Linux 的靜態執行檔可以用 `make dist` 產生，測試也能在 `GOOS=linux` 下編譯，但還沒有在 Linux 主機上實際跑過測試。`--explain` / `--confirm` 的近似指令會依作業系統使用 macOS（BSD）或 GNU/Linux 語法；GNU 版的排序 pipeline 已經在 macOS 上用 GNU coreutils 的 `gstat` 實際執行驗證過。
- 未支援的說法會回報「看不懂這個詞」；詞彙表在 `internal/query/lexicon.go`，擴充時請一併加測試。

## Development

```
main.go                     CLI：參數、輸出、錯誤訊息
internal/query/tokenize.go  中英混合斷詞（字典最長匹配、中文數字）
internal/query/lexicon.go   所有認得的詞彙
internal/query/parse.go     詞 → Query
internal/query/explain.go   --explain 與近似 find 指令
internal/search/search.go   唯讀走訪與比對
```

`make check` 是完成標準：格式、vet、測試、build 全過。`make dist` 產生各平台的靜態執行檔與 `SHA256SUMS`（放在 `bin/`，不納入版本控制）。
