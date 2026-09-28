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
scp -J user@bastion bin/nlf-0.4.0-linux-amd64 bin/SHA256SUMS user@target:~/

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
sudo install -m 755 nlf-0.4.0-linux-amd64 /usr/local/bin/nlf

# 不需要 sudo：安裝到自己的目錄
mkdir -p ~/.local/bin
install -m 755 nlf-0.4.0-linux-amd64 ~/.local/bin/nlf
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
- 在 Linux 上，`--explain` 與 `--confirm` 顯示的是 GNU/Linux 語法的近似指令（標示為「近似的 GNU/Linux 指令」）。和 macOS 版有兩個差別：排序用的 stat 格式（GNU coreutils 用 `stat -c '%s %n'` / `stat -c '%.9Y %n'`，macOS 用 `stat -f '%z %N'` / `stat -f '%Fm %N'`，修改時間都含奈秒），以及 `-newermt` 的時間格式（GNU find 以奈秒比較，所以寫成 `'2026-09-27 23:59:59.999999999'`；BSD find 以整秒比較，寫成 `'2026-09-27 23:59:59'`；因此在 macOS 上，邊界不是整秒時（例如「最近 7 天」這類相對時間）可能差不到 1 秒）。find 的其他部分與 grep、sort、cut、head 兩邊相同。nlf 本身不會執行這些指令。
- 完整測試會在 macOS 與 Linux 上執行：GitHub Actions 的 `ubuntu-latest` 已實際跑過 unit test、race detector 與 runtime smoke test（見下方 CI 小節）。

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
-0, --null       每個路徑後面接 NUL（\0）而不是換行，給 xargs -0 使用
-d, --dir PATH   搜尋位置（預設為目前目錄）
--max-depth N    最多往下 N 層（搜尋位置裡的檔案是第 1 層）
--min-depth N    只列出至少 N 層深的檔案
-h, --help       說明與範例
--version        版本
```

預設輸出為每行一個路徑，可以直接接 pipe；但檔名裡如果有換行，一筆結果會被拆成兩行。要把結果交給其他程式時，建議用 `-0`：

```bash
nlf -0 "找最近 7 天修改的 go 檔" | xargs -0 -r wc -l
```

- `-r` 讓 GNU xargs 在沒有任何輸入時不執行指令；macOS 的 xargs 也接受 `-r`（沒有輸入時本來就不執行）。
- `-0` 不能和 `-l`、`--explain`、`--help`、`--version` 同時使用（exit 2）。
- `-0` 可以和 `--confirm` 一起用：執行計畫與提示寫到 stderr，stdout 仍然只有以 NUL 結尾的路徑。

不能一起用的 flag 會直接報錯（exit 2），不會默默忽略其中一個：

- `--explain` 不能和 `--confirm`、`-l`、`-0` 同時使用：`--explain` 不搜尋，沒有檔案可以列出。
- `--help`、`--version` 只印文字、不搜尋，所以不能和其他 flag（`--explain`、`--confirm`、`-l`、`-0`、`-d/--dir`、`--max-depth`、`--min-depth`）一起用，兩者也不能同時使用。查詢文字（在 flag 前面或後面都一樣）不算 flag，仍然允許，但會被忽略、也不會檢查（`nlf --help go files` 照樣印出說明，exit 0）。
- `-d/--dir` 只能給一次，值不能是空字串：`--dir ""`、`-d ""`、`--dir=` 會報 `--dir needs a directory`（`-d ""` 是 `-d needs a directory`），`--dir a --dir b` 會報 `-d/--dir can be given only once`。
- 可以一起用的組合：`--confirm` 搭配 `-l`、`-0` 或 `--dir`；`--explain`、`-l`、`-0` 搭配 `--dir`；`--max-depth`、`--min-depth` 可以搭配 `--dir`、`--explain`、`--confirm`、`-l`、`-0`，兩者也可以一起用。

### 搜尋深度（`--max-depth` / `--min-depth`）

```bash
nlf --max-depth 1 "找 go 檔"          # 只找搜尋位置裡面的 go 檔，不往子目錄找
nlf --min-depth 2 --dir ~/work "找 md 檔"   # 只找子目錄裡（至少第 2 層）的 md 檔
```

- **深度的定義（與 `find` 相同）：** 搜尋位置本身是第 0 層，直接在裡面的檔案與目錄是第 1 層，依此類推。搜尋位置是 symlink 時（例如 macOS 的 `/tmp`）一樣從 0 算起；搜尋位置是一個檔案時，那個檔案就是第 0 層（`--max-depth 0` 會列出它，`--min-depth 1` 不會）。
- **`--max-depth N` 在走訪時就剪枝：** 第 N 層的目錄不會被讀取，所以更深的檔案不會被列出、開啟或檢查，也不會出現更深層的權限錯誤。`--max-depth 0` 連搜尋位置本身的目錄都不讀取。
- **`--min-depth N` 只過濾檔案：** 比第 N 層淺的檔案在讀取檔案資訊與內容之前就略過；目錄仍然照常走訪，排除的目錄在任何深度都一樣會被剪掉。
- **值的規則：** 0 到 4096 的十進位整數，只接受數字（`007` = 7），`--max-depth 2` 與 `--max-depth=2` 都可以；每個 flag 只能給一次。flag 後面的下一個參數一定當成值，即使它以 `-` 開頭（`--max-depth -1` 是不合法的值，不是另一個 flag）。
- **錯誤（exit 2，搜尋之前就報錯）：**
  - 沒有值或空值：`--max-depth needs a number`
  - 不是 0–4096 的整數（`-1`、`1.5`、`abc`、`+1`、`4097`…）：`--max-depth needs a whole number from 0 to 4096, got "-1"`
  - 重複：`--max-depth can be given only once`
  - 最小大於最大：`--min-depth 3 is greater than --max-depth 1, so no file can match`（不會自動對調）

  `--min-depth` 的訊息相同，只是換成 `--min-depth`。
- **沒有自然語言寫法：** 深度只能用 flag 指定。查詢裡的 `最多 N 個`、`top N` 仍然是數量上限，不是深度。

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
- 執行計畫的條件會列出搜尋深度（例如 `depth:  at most 1 level (…)`），近似指令也會帶上 `-maxdepth` / `-mindepth`。
- 計畫裡的指令與 `--explain` 一樣只是近似、供理解用，並依作業系統顯示 macOS 或 GNU/Linux 語法；nlf 不會執行它。
- `--explain` 與 `--confirm` 不能同時使用（exit 2）；`--explain` 也不能和 `-l`、`-0` 一起用（見上方 Usage）。
- `--explain` 不檢查搜尋位置是否存在：位置不存在時照樣顯示理解結果與近似指令（exit 0），實際搜尋時才會報錯（exit 2）。

### 支援的條件

不同條件之間是 AND（副檔名、檔名、內容、大小、時間…）；同一類的條件可以用「或 / or」組成 OR 群組（見下方「Boolean OR」）。多個副檔名之間本來就是「任一符合」。

| 條件 | 範例 |
|------|------|
| 副檔名 | `.go`、`*.md`、`go files`、`pdf 檔`、`go 或 md 檔`、`圖片`、`影片`、`images`、`副檔名是 heic`、`找 ovpn 檔`、`find pem files` |
| 內容 | `containing "TODO"`、`with TODO`、`包含 TODO`、`內容有「錯誤訊息」`、`ignore case`、`不分大小寫` |
| 修改時間（相對） | `last 7 days`、`in the past 3 hours`、`last hour`、`older than 30 days`、`30 days ago`、`最近 7 天`、`最近 30 分鐘`、`3 小時內`、`超過 30 天`、`3 天前` |
| 修改時間（日期） | `today`、`yesterday`、`今天`、`昨天`、`前天`、`本月`、`上個月`、`今年`、`去年`、`2026 年`、`2026 年 9 月`、`9 月`、`2026-09-28`、`9/28`、`September 2026`、`Sep 28 2026`、`9 月 1 日到 9 月 15 日`、`昨天 23:00 到今天 02:00`、`凌晨 1 點到 5 點`、`since Sep 1`（完整語法見下方「日期與時間」） |
| 檔案大小 | `larger than 10MB`、`smaller than 1KB`、`at least 1GB`、`> 2gb`、`大於 10MB`、`10MB 以上`、`小於 1KB`、`最大 10MB` |
| 數量 | `top 10`、`first 5`、`最多 10 個`、`前 10 個`、`只要十個` |
| 最大 / 最新 N 個 | `10 largest files`、`最大的 5 個檔案`、`前 3 大`、`newest 3`、`最新的 3 個`、`最近修改的 5 個檔案` |
| 排除目錄 | `exclude node_modules and .git`、`排除 vendor、build 目錄`、`除了 dist 以外`、`except dist dir`、`排除 src/generated`、`排除 ./src/generated` |
| 搜尋位置 | `in ~/Documents`、`under /tmp`、`在 ./src`，或 `--dir` |

### 日期與時間

所有日期與時間條件都轉成修改時間（mtime）的**半開區間**：`開始 <= mtime < 結束`，使用執行 nlf 當下的**本機時區**。`--explain` / `--confirm` 會直接顯示這個區間與時區，例如 `2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00`、`CST (+08:00)`。搜尋本身只用 Go 讀取檔案的 mtime，不依賴 `find`、`date` 或任何 shell 指令，所以在 macOS 與 Linux 上的行為相同。

```bash
nlf '找 2026 年 9 月的 pdf 檔'
nlf '找 9 月修改的 yml 檔'
nlf '查 2026 年 9 月 28 日改了什麼檔案'
nlf '找 9 月 1 日到 9 月 15 日修改的 log'
nlf '找最近 6 小時修改的 conf'
nlf '找昨天 23:00 到今天 02:00 修改的 log'
nlf '找本月修改的 yaml'
nlf 'pdf files modified in September 2026'
nlf 'files modified on 2026-09-28'
nlf 'log files modified from Sep 1 to Sep 15'
nlf 'conf files modified in the last 6 hours'
```

| 類型 | 寫法 | 區間 |
|---|---|---|
| 年 | `2026 年`、`in 2026`、`from 2025`、`during 2026`、`今年`、`去年`、`this year`、`last year` | 1/1 00:00 到隔年 1/1 00:00 |
| 月 | `2026 年 9 月`、`2026-09`、`2026/09`、`September 2026`、`9 月`、`九月`、`Sep`、`今年 9 月`、`去年 9 月`、`September last year`、`本月`、`這個月`、`上個月`、`this month`、`last month` | 1 日 00:00 到下個月 1 日 00:00 |
| 日 | `2026-09-28`、`2026/09/28`、`2026 年 9 月 28 日`、`2026 年 9 月 28 號`、`9 月 28 日`、`9/28`、`Sep 28 2026`、`Sep 28, 2026`、`September 28`、`今天`、`昨天`、`前天`、`today`、`yesterday`、`the day before yesterday` | 00:00 到隔天 00:00 |
| 日期區間 | A 到 B、A 至 B、從 A 到 B、from A to B、A through B、between A and B，例如 `9 月 1 日到 9 月 15 日`、`9 月 1 日到 15 日`、`9/1 到 9/15`、`2026-09-01 至 2026-09-30`、`2025 年到 2026 年` | A 的開始 到 B 的**結束**（結束日含在內） |
| 時間區間 | `今天 2 點到 4 點`、`今天 1:00 到 3:30`、`昨天 23:00 到今天 02:00`、`2026-09-28 01:00 到 03:30`、`9 月 28 日凌晨 1 點到 5 點`、`凌晨 1 點到 5 點`、`下午 1 點到 5 點`、`晚上 10 點到 11 點半`、`today between 2am and 4am`、`yesterday from 23:00 to today 02:00`、`on 2026-09-28 between 01:00 and 03:30` | 開始時刻 到 結束時刻（結束時刻不含） |
| 到現在 | `從 9 月 1 日到現在`、`9 月 1 日至現在`、`9 月 1 日到目前`、`from Sep 1 until now`、`from Sep 1 to now` | 開始 到 執行當下這一刻（不含），未來的 mtime 不算 |
| 開放區間 | `從 9 月 1 日`、`9 月 1 日以來`、`從 9 月 1 日開始`、`since September 1` | 只有開始，沒有結束（未來的 mtime 也算） |
| 單邊比較 | `2026-09-01 以前`、`9 月以後`、`2025 年或以前`、`2026-09-01 以後含當日`、`before Sep 1`、`after September`、`on or before 2025`、`on or after Sep 1` | 見下方「絕對日期比較」 |
| 相對時間 | `最近 1 小時`、`最近 6 小時`、`最近 30 分鐘`、`過去 2 天`、`last hour`、`last 6 hours`、`past 30 minutes`、`in the last 2 days`、`in the last month` | 現在往前推一段時間，沒有結束 |

規則：

- **區間的結束：** 結束是日期時，包含那一整天：`9/1 到 9/15` = `[9/1 00:00, 9/16 00:00)`。結束是時刻時，不包含那一刻：`1 點到 5 點` = `[01:00, 05:00)`。結束是「現在 / now」時，結束在執行當下這一刻（不含），所以 mtime 在未來的檔案不算；`since`、`從 A`、`A 以來` 則完全沒有上限。
- **開始必須早於結束：** `9/20 到 9/10` 會報「開始時間必須早於結束時間」，不會自動對調。
- **沒寫年份：** 使用今年，也就是執行當下的年份。例如 `9 月`、`9/28`、`September 28`。
- **沒寫日期的時刻：** 使用今天。例如 `凌晨 1 點到 5 點`。區間的結束如果省略日期或月份，會沿用開始的日期或月份（`2026-09-28 01:00 到 03:30`、`9 月 1 日到 15 日`）。上午、下午也會沿用（`下午 1 點到 5 點` = 13:00–17:00）。
- **斜線日期固定是「月/日」：** `03/04` 是 3 月 4 日，不會依 locale 猜測。有歧義的寫法一律報錯：`03/04/2026`、`04-03-2026`、兩位數年份 `26-09-28`、`2026.09.28`。
- **時刻格式：** `HH:MM`、`H:MM`（24 小時制）；`2am`、`11:30pm`、`2 pm`；`凌晨 / 早上 / 上午 / 中午 / 下午 / 晚上` + `N 點 [M 分]` 或 `N 點半`。單一時刻（例如 `2 點修改的`）不是區間，會報錯。
- **一律驗證、不會自動修正：** `2026-02-29`、`2026-13-01`、`2026-04-31`、`2026-00-10`、`25:00`、`12:99`、`晚上 12 點` 都會報錯。`2024-02-29` 是合法日期。
- **`last month` 與 `in the last month` 不同：** `last month`、`上個月` 是上一個日曆月；`in the last month`、`past month`、`過去一個月` 是往前推 30 天。`last year` 同理。`last week` 仍是往前推 7 天。
- **英文的純數字年份要有介系詞：** `in 2026`、`from 2025`、`during 2026` 才是年份。`2026 files` 仍然是「最多 2026 個」。
- **`may`：** 單獨出現時，只有在前面有介系詞（`in may`）或後面有日期、年份時才當成五月，避免把動詞 may 誤判成月份。
- **日曆單位用日曆計算：** 日、月、年都用本機時區的日曆計算，不用 24 小時的倍數，所以夏令時間切換的那一天長度是 23 或 25 小時，邊界仍然在午夜。
- **可以和其他條件組合：** 副檔名、內容、大小、排除、排序、數量都可以一起用，例如 `找 9 月最大的 10 個 log`、`找昨天修改而且包含 "ERROR" 的 log`。多個時間條件會取交集；沒有交集時報錯。

#### 絕對日期比較（以前 / 以後）

```bash
nlf '找 2026-09-01 以前修改的檔案'
nlf 'files modified on or after Sep 1'
nlf '找 9 月以後修改的 yml'
nlf '找 2025 年或以前、檔名以 .bak 結尾的檔案'
nlf '找 9 月 1 日以前或 9 月 20 日以後修改的 log'
```

日期、月份、年份都先換成它的日曆區間 `[開始, 結束)`，比較用語只決定取哪一邊：

| 寫法 | 以 Sep 1 為例 | 規則 |
|---|---|---|
| `before Sep 1`、`9 月 1 日以前`、`2026-09-01 之前` | mtime < Sep 1 00:00 | 不含當天 |
| `on or before Sep 1`、`2026-09-01 或以前`、`2026-09-01 以前含當日` | mtime < Sep 2 00:00 | 含當天 |
| `after Sep 1`、`9 月 1 日以後`、`2026-09-01 之後` | mtime >= Sep 2 00:00 | 不含當天，從隔天開始 |
| `on or after Sep 1`、`2026-09-01 或以後`、`2026-09-01 以後含當日` | mtime >= Sep 1 00:00 | 含當天 |

- **`since` 和 `after` 不一樣：** `since Sep 1`（`從 9 月 1 日`、`9 月 1 日以來`）等於 `on or after Sep 1`，從 Sep 1 00:00 開始；`after Sep 1` 則是從 **Sep 2** 00:00 開始。`--explain` 會直接顯示實際的邊界，例如 `after 2026-09-02 00:00:00 (mtime >= 2026-09-02 00:00:00)`。
- **月份與年份用同一套規則：** `before September` = mtime < 9/1；`on or before September` = mtime < 10/1；`after September` = mtime >= 10/1；`on or after September` = mtime >= 9/1。`2025 年以前` = mtime < 2025-01-01；`2025 年以後` = mtime >= 2026-01-01。沒寫年份的月份使用今年。
- **`N 月以前` 和 `N 月前` 不同：** `9 月以前`、`3 月之前` 是「9 月 / 3 月開始之前」（日曆）；只有一個「前」的 `3 月前`、以及 `3 個月前` 仍然是相對時間（往前推 90 天）。要表示一段時間請寫 `3 個月前`、`90 天前`。
- **`before` 後面接數字時是相對時間：** `before 30 days`、`older than 30 days`、`3 days ago` 的行為不變。`before 2025` 則是年份，因為 4 位數字在 `before` 後面會被當成年份。
- **不支援時刻：** `before 14:00`、`after 3pm`、`下午 3 點以前`、`after 2026-09-01 14:00` 都會報錯。時刻沒有指定哪一天時意思不明確；需要時請寫成時間區間，例如 `今天 1 點到 5 點`。
- **比較之後不能再接區間：** `after Sep 1 to Sep 10` 會報錯。區間請寫成 `9 月 1 日到 9 月 15 日`，或用兩個比較加上「且」：`9 月 1 日以後且 9 月 15 日以前` = `[9/2, 9/15)`。
- **組合與 OR：** 單邊比較就是一般的修改時間條件，所以可以和副檔名、檔名、內容、大小一起用（AND），也可以和其他時間條件組成 OR：`before Sep 1 or after Sep 20` = mtime < 9/1 或 mtime >= 9/21。`on or before Sep 1 or on or after Sep 20` 裡，前後兩個 `or` 屬於比較用語，只有中間的 `or` 是 Boolean OR。互相矛盾的條件（`9 月 20 日以後且 9 月 10 日以前`）在解析時就會報錯。
- **缺少對象會報錯：** 單獨的 `before`、`after`、`on or before`、`on or after`、`以前`、`之後`、`或以前`、`或以後` 都會報錯。

### 檔名搜尋

用明確的「檔名 / 名稱 / 名字 / filename / name」加上比對方式，搜尋檔案的**檔名本身**：

```bash
nlf '找檔名是 application.yml'
nlf '找檔名包含 config 的 yml'
nlf '找檔名以 app- 開頭的 log'
nlf '找檔名以 .bak 結尾'
nlf '找檔名符合 "core.*"'
nlf '找檔名是 nginx.conf 或 httpd.conf'
nlf 'find files named kubeconfig'
nlf 'find yaml files whose name contains config'
```

| 比對方式 | 寫法 | 意思 |
|---|---|---|
| 完全相同 | `檔名是 application.yml`、`檔名叫 kubeconfig`、`名稱是 nginx.conf`、`檔名等於 .env`、`名為 docker-compose.yml 的檔案`、`filename is nginx.conf`、`file named .env`、`files named kubeconfig`、`name equals .env` | 檔名 == 值 |
| 包含 | `檔名包含 config`、`名稱有 backup 的 sql`、`名字包含 nginx`、`filename contains config`、`files whose name contains backup` | 檔名含有這段文字 |
| 開頭 | `檔名以 app- 開頭`、`名稱開頭是 backup-`、`名字從 nginx 開始`、`filename starts with app-`、`name begins with nginx` | 檔名以這段文字開頭 |
| 結尾 | `檔名以 .bak 結尾`、`名稱結尾是 .old`、`name ending in .old`、`files whose name ends with .disabled` | 檔名以這段文字結尾 |
| glob | `檔名符合 "*.conf"`、`名稱符合 "app-*.yml"`、`檔名匹配 "core.*"`、`filename matches "*.conf"`、`filename glob "core.*"` | Go glob（`path.Match`）：`*`、`?`、`[...]` |

規則：

- **只比對檔名，不比對路徑：** `/etc/nginx/nginx.conf` 的檔名是 `nginx.conf`。搜尋位置（`在 /etc/nginx`、`--dir`）的語意不變。
- **檔名比對區分大小寫；副檔名比對仍然不分大小寫。** `檔名是 Config.yml` 不會找到 `config.yml`。在 macOS 預設不分大小寫的 APFS 上也一樣，這是 nlf 自己的比對規則。
- **`不分大小寫` / `ignore case` 只影響內容搜尋。** 查詢裡有檔名條件、卻沒有內容條件時，加上它會報錯，不會默默改變檔名比對。需要時請用 glob 列出大小寫，例如 `檔名符合 "[Cc]onfig*"`。
- **值保持原樣：** 大小寫、點開頭（`.env`、`.gitignore`）、`--weird-name`、`name[1].txt` 都照原樣比對。含空白或中文的檔名請加引號：`檔名是 "foo bar.conf"`、`檔名是 "中文設定檔.yml"`。
- **完全相同、包含、開頭、結尾都是一般文字比對，只有 glob 使用萬用字元。** 例如 `檔名是 name[1].txt` 就是字面上的 `name[1].txt`。`檔名以 .tar.gz 結尾` 也是一般的結尾比對，和副檔名無關。
- **glob 不是正規表示式：** 只支援 `*`、`?`、`[...]`，不支援 `**`、`{a,b}`、regex。`*` 也會比對到點開頭的檔名（`*` 找得到 `.env`）。格式錯誤的 glob（例如 `"[abc"`）在解析時就會報「檔名 glob pattern 不合法」（exit 2），不會搜尋。
- **和其他條件是 AND：** `找檔名包含 config 的 yml` = 檔名包含 config AND 副檔名 .yaml/.yml；`找今天修改而且檔名包含 config 的檔案`；`找檔名是 nginx.conf，而且內容包含 "listen 443"`。檔名條件不會推導成副檔名，反過來也不會。
- **一定要寫出「檔名 / 名稱 / 名字 / name」：** `找包含 ERROR 的 log` 仍然是內容搜尋；`找檔名包含 ERROR 的 log` 才是檔名。`go 或 md 檔` 仍然是副檔名。
- **多個檔名條件：** 用「且」連接時是 AND（`檔名包含 config 且檔名以 app- 開頭`）；用「或」連接時是 OR（見下一節）。
- **OR 可以省略比對方式：** `檔名包含 prod 或 staging`、`檔名是 nginx.conf 或 httpd.conf`、`檔名以 app- 開頭或 api-`、`檔名符合 "*.conf" 或 "*.cfg"`。省略時，「或」後面沿用前一個比對方式；寫出來時用自己的方式（`檔名包含 prod 或檔名是 staging.yml`）。不同比對方式也可以用 OR 連接，因為它們都是檔名條件。
- **效能：** 檔名在讀取檔案資訊與內容之前就先比對，檔名不符的檔案不會被開啟。
- **`--explain` / `--confirm`：** 會顯示 `檔名  包含 "config" (區分大小寫)`。近似的 find 指令用 `-name`（不是 `-iname`），一般文字裡的 `* ? [` 會先跳脫。find 的 glob 和 Go glob 大致相同但不完全一樣，實際行為以 nlf 為準。

### Boolean OR

用「或 / 或者 / 或是 / or」連接**同一類**的條件，符合其中任何一個即可：

```bash
nlf '找今天或昨天修改的檔案'
nlf '找包含 "ERROR" 或 "FATAL" 的 log'
nlf '找 8 月或 9 月修改的 yml'
nlf '找 go 或 yml 或 json 檔'
nlf '找大於 1GB 或小於 1KB 的檔案'
nlf 'log files containing "ERROR" or "panic"'
```

| 類型 | 例子 | 意思 |
|---|---|---|
| 副檔名 | `go 或 md 檔`、`go or yml or json files`、`go 檔或 md 檔` | 副檔名是其中任何一個 |
| 檔名 | `檔名是 nginx.conf 或 httpd.conf`、`檔名包含 prod 或 staging`、`filename matches "*.conf" or "*.cfg"`、`檔名包含 prod 或檔名是 staging.yml` | 符合其中任何一個檔名條件 |
| 內容 | `包含 "ERROR" 或 "FATAL"`、`包含 "ERROR" 或包含 "FATAL"`、`containing "foo" or "bar"`、`包含 "A" 或 "B" 或 "C"` | 內容至少包含其中一個 |
| 修改時間 | `今天或昨天`、`8 月或 9 月`、`本月或上個月`、`2026-09-01 或 2026-09-15`、`today or yesterday`、`in August or September`、`今天 01:00 到 03:00 或 05:00 到 06:00` | mtime 落在其中任何一個區間 |
| 檔案大小 | `大於 1GB 或小於 1KB`、`larger than 1GB or smaller than 1KB` | 符合其中任何一個大小條件 |

**OR 和 AND 怎麼組合：** OR 只把「或」前後相鄰的兩個同類條件組成一個群組，這個群組再和其他所有條件做 AND。沒有其他優先順序規則，也不需要猜。例如：

```bash
nlf '找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log'
```

等於：

```
(今天 OR 昨天) AND ("ERROR" OR "FATAL") AND 副檔名 .log
```

`找今天或昨天修改的 log` 是 `副檔名 .log AND (今天 OR 昨天)`，不會變成 `今天 OR (昨天 AND .log)`。

規則：

- **兩邊必須同一類：** `大於 1GB 或最近 1 小時`、`包含 "ERROR" 或 9 月修改`、`pdf 或大於 10MB` 會報「目前 OR 只能連接同類條件」（英文為 `OR currently requires conditions of the same type`）。排除、排序、數量、搜尋位置、不分大小寫這類設定也不能用「或」連接。
- **不完整的 OR 會報錯：** 前面沒有條件（`或 今天`）、後面沒有條件（`今天 或`）、連續兩個（`今天 或 或 昨天`）都會報錯，不會猜。
- **AND 的寫法不變：** `包含 "A" 且包含 "B"`、`包含 "A" 和 "B"` 仍然是「同時包含」。「且、而且、並且、和」以及逗號連接的條件都是 AND。
- **比較用語不是 OR：** `10MB or more`、`1GB or smaller`、`3 days or older`、`10MB 或以上` 裡的 or / 或 是比較用語的一部分。`exclude vendor or build` 仍然是兩個排除目錄。
- **日期 OR 是多個區間的聯集，不會合併：** `1 月或 9 月` 就是兩個區間，不會變成 1 月到 9 月。沒寫日期的時刻沿用前一個區間的日期：`昨天 1 點到 3 點或 5 點到 6 點` 的兩段都在昨天。
- **和其他時間條件取交集：** OR 群組的每個區間都和其他時間條件取交集；如果沒有任何一個區間可能成立，會報「修改時間條件互相矛盾」，例如 `今天或昨天，8 月`。
- **內容 OR 只讀一次檔案：** 不論有幾個內容條件（AND 或 OR），每個檔案都只開啟、讀取一次，同時比對所有文字。binary 判斷、不分大小寫、跨 chunk 的處理都和一般內容搜尋相同。
- **`--explain` / `--confirm` 會標出 OR：** 例如 `內容包含  "ERROR" 或 "FATAL" (符合任一個即可，區分大小寫)`，以及 `修改時間  任一區間（OR）` 底下逐行列出每個區間。近似的 find 指令用 `\( … -o … \)` 表示大小與時間的 OR，用 `grep -e A -e B` 表示內容的 OR；這些指令只是近似，nlf 不會執行它們。
- 不支援 NOT、括號，也不支援不同類條件之間的 OR。

`--explain` 範例（在 macOS 上執行；在 Linux 上標題會是「近似的 GNU/Linux 指令」）：

```text
$ nlf --explain "找出最近 7 天修改、大於 1MB 的 go 檔，排除 vendor，最多 10 個"
解析結果：
  搜尋位置       .
  副檔名         .go
  修改時間       晚於 2026-09-20 22:39:05 (mtime >= 2026-09-20 22:39:05)
  時區           CST (+08:00)，本機時間
  檔案大小       > 1 MB
  排除目錄       vendor
  數量上限       10

近似的 macOS 指令（僅供參考，nlf 不會執行它）：
  find . -type d \( -iname vendor \) -prune -o -type f -iname '?*.go' -size +1048576c -newermt '2026-09-20 22:39:05' -print | head -n 10
```

## Behavior details

- 大小用二進位單位：1 KB = 1024 bytes，`m` / `mb` 都是 MB（不是分鐘；分鐘請寫 `min` 或 `分鐘`）。
- 相對時間中，「月」= 30 天、「年」= 365 天；「7 天」是從現在往前推 7×24 小時（`最近 3 月`、`3 月前`、`3 個月前`、`3 年前` 都是相對時間；`3 月以前` 則是日曆比較，見「絕對日期比較」）。日曆日期（`今天`、`9 月`、`2026 年`）則以本機時區的日曆為界，見「日期與時間」。
- 「3 天前」「30 days ago」表示「修改時間早於 3 天前」。
- 內容搜尋是逐字比對（不是正規表示式），預設區分大小寫；第一次讀到的內容（最多 64 KB）含 NUL 位元組的檔案視為二進位並略過（類似 `grep -I`）。近似指令裡的 grep 判斷方式不同：macOS 的 BSD grep 只檢查前 32 KB，所以 NUL 出現在 32 KB 到 64 KB 之間時，grep 會當成文字、nlf 會略過；GNU grep 的判斷方式又不一樣。不是合法 UTF-8 的內容，在 UTF-8 locale 下可能被 grep（例如 GNU grep）當成二進位，nlf 則照樣比對。
- 副檔名比對不分大小寫。檔名必須在副檔名前面還有字：只叫 `.go` 或 `.bashrc` 的檔案沒有副檔名（`.bashrc` 不算 `bashrc` 檔，`.go` 不算 go 檔），近似指令因此寫成 `-iname '?*.go'`。任何英數字詞後面接「檔／檔案／files」等名詞就視為副檔名（`找 ovpn 檔` = `*.ovpn`）；沒有名詞時只認得內建別名（`go`、`python`、`圖片`…）或 `.ext`、`*.ext`、`副檔名 xxx`。常見英文形容詞（`large files`、`old files`）會報錯，不會被當成副檔名。
- 排除目錄以名稱比對（不分大小寫）。含 `/` 時是相對於搜尋位置的路徑：`src/gen`、`./src/gen`、`src/gen/`、搜尋位置底下的絕對路徑或 `~/…` 都可以；路徑在搜尋位置之外會報錯。排除的名稱與路徑都是字面比對：`* ? [ \` 沒有萬用字元的意思（`排除 "a[1]"` 只排除名叫 `a[1]` 的目錄，不會排除 `a1`），近似指令會先跳脫它們。搜尋位置本身永遠不會被排除，即使它的名稱就是要排除的名稱（`--dir vendor "exclude vendor"` 仍搜尋 vendor 裡面，只排除更深層的 vendor）。路徑的大小寫依檔案系統判斷：大小寫不同的寫法，只有在檔案系統把它解析成同一個目錄時（例如 macOS 預設的 APFS）才會排除。
- 只能排除目錄。`排除 .go`、`exclude *.log`、`exclude go files` 這類看起來是檔案類型的寫法會報錯；如果真的是目錄名稱，請加「目錄」／`dir` 或引號。
- `exclude`、`excluding`、`排除`、`skip`、`跳過`、`ignore`、`忽略` 直接表示排除目錄；`except`、`除了`、`不要` 後面要有「目錄／以外／dir」等字才算排除，否則報錯。`without`、`不包括`、`不包含` 是否定條件，目前會報錯。
- 標點符號（引號外）：
  - 括號 `( ) （ ）` 與 `! ！` 保留給分組與否定，一律報錯，不會被略過；要搜尋這些字元請加引號，例如 `包含 "(x)"`。
  - `? ？` 只能放在整個查詢的最後（`找 go 檔？`、`find go files?`），放在其他位置會報錯；glob 裡的 `?` 請加引號，例如 `檔名符合 "core.?"`。
  - 標點緊接在未加引號的值後面時，無法確定是否屬於這個值，會報錯要求加引號：`檔名是 core.`、`檔名符合 core.?`、`包含 錯誤？`、`排除 vendor.`、`*.log?`、`副檔名 heic?` 都要寫成 `檔名是 "core"` 或 `檔名是 "core."` 這類明確的寫法。
  - 分隔用的 `，、。；：` 和 `,` `;` 不受影響：`包含 錯誤。` 仍是搜尋「錯誤」，`包含 錯誤訊息，大於 1KB` 仍是兩個條件；但 `包含 錯誤？` 會報錯，因為 `？` 可能是要搜尋的字。
  - 緊接在前一個字後面、只由 `.` 組成的一串（`包含 "x".`、`找 go 檔..`）只是句點，不會變成搜尋位置；要指定目前或上一層目錄請用空白隔開，例如 `在 . 找 go 檔`、`在 .. 找 go 檔`。`排除 .`、`排除 ./`、`排除 ..`、`排除 /` 會報錯。
  - 只有標點（`?`、`。`）的查詢視為空查詢，會報錯。
- 相對路徑的結果如果以 `-` 開頭，一律印成 `./-…`（例如 `./-rf.txt`），所有輸出模式都一樣（和 `find .` 相同），避免被其他程式當成選項。
- 檔名不可能包含 NUL，所以 `-0` 的輸出一定能正確切開。不是合法 UTF-8 的檔名位元組會原樣輸出，不會轉換；macOS 的 APFS 本身不允許這種檔名。
- 只回傳一般檔案，不回傳目錄；不跟隨目錄內的 symlink（搜尋位置本身是 symlink 時會跟隨，例如 `/tmp`）。
- 隱藏檔與 `.git` 會被搜尋，需要時請加 `排除 .git`。
- 沒有排序時依名稱順序輸出，達到數量上限就停止；有「最大/最新」時會先走完整棵樹再排序。
- `--explain` 顯示的 find 指令是近似的，僅供參考。時間區間的邊界（`-newermt` 取邊界前 1 奈秒；GNU/Linux 完全相同，macOS 的 BSD find 以整秒比較，只有整秒邊界完全相同，相對時間的邊界可能差不到 1 秒）、最大／最新排序的同分與次秒精度（`LC_ALL=C sort -k1,1nr -k2`，同分時和 nlf 一樣依路徑的位元組順序）都和 nlf 相同。仍然可能不同的地方：
  - 未排序時的輸出順序：nlf 依名稱順序，find 依目錄讀取順序；因此沒有排序的 `top N`（`head -n N`）可能選到不同的檔案。
  - 檔名 glob 的 `[!…]`：find 當成否定，nlf（Go `path.Match`）不是，否定請寫 `[^…]`。
  - 大小寫：非 ASCII 字元的大小寫轉換（副檔名、排除名稱、`ignore case`）可能和 find、grep 不同。
  - binary 判斷與不是合法 UTF-8 的內容（見上方內容搜尋）。
  - `--explain` 不檢查搜尋位置是否存在，find 則會對不存在的位置報錯。
  - 深度：`-mindepth N` / `-maxdepth N` 緊接在起點後面（GNU find 對放在其他條件後面的 global option 會警告）。有排除目錄時，最小深度改寫成 `-type f -path '<搜尋位置>/*/*'`（每一層一個 `/*`），不用 `-mindepth`：BSD 與 GNU find 的 `-mindepth` 在較淺的層級不套用任何條件，包括 `-prune`，所以較淺的排除目錄仍會被走訪；nlf 則在任何深度都會剪掉排除的目錄。
  - `--max-depth 0` 搭配沒有權限讀取的目錄時，nlf 完全不開啟它（沒有結果，exit 1），BSD find 仍會回報 Permission denied。
- nlf 本身用 Go 走訪檔案，不會執行任何 shell 指令，也不會執行上面的近似指令。
- 沒有權限讀取的目錄會略過；檔案只有在需要讀取內容（內容搜尋）時才會因權限不足而略過，否則仍會列出（與 `find` 相同）。略過的位置在結束時列在 stderr；nlf 不會提權。

## Known limitations

- 不支援不同類條件之間的 OR（`大於 1GB 或今天`）、括號、否定（「不包含 X」「without X」）、正規表示式（包括檔名的正規表示式；檔名請用 glob）、以時刻為對象的單邊比較（`before 14:00`、`下午 3 點以前`）。
- 未加引號的搜尋文字會一直延伸到空白或標點（`包含 API連線失敗`）。如果後面緊接著 nlf 認得的詞（例如 `包含 設定檔`，「檔」是關鍵字），因為無法確定範圍，會報錯要求加引號；只有「的」「且」「和」這類連接詞可以直接接在後面（`包含TODO且大於1MB`）。空字串 `""` 會報錯。
- 未加引號的路徑會一直延伸到空白或標點，可以包含中文（`在 ~/文件 找 pdf 檔`）。路徑後面請加空白，不要直接接其他字；路徑中有空白時要加引號，例如 `在 "~/My Docs"`。
- 數量上限最多 2147483647；時間最多約 292 年；搜尋深度最多 4096（`find` 接受任意整數）；超過會報錯。
- 主要在 macOS 上開發。Linux 由 GitHub Actions 的 `ubuntu-latest` 實際執行 unit test、race detector 與 runtime smoke test（`make dist` 產出的靜態執行檔也會在 runner 上實際執行）。`--explain` / `--confirm` 的近似指令會依作業系統使用 macOS（BSD）或 GNU/Linux 語法；GNU 版的排序 pipeline 已經在 macOS 上用 GNU coreutils 的 `gstat` 與 `gsort` 實際執行驗證過；整個近似指令（含 GNU find 與 grep）則由 CI 的 `ubuntu-latest` 實際執行比對。
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

### CI

`.github/workflows/nlf-ci.yml` 在 `ubuntu-latest` 與 `macos-latest` 上各跑一次：`make fmt-check`、`make vet`、`go test -count=1 -v`（Linux 不允許任何測試被略過；macOS 只允許沒有 `gstat` 時略過 `TestGNUSortPipeline`）、`go test -race`、`go test -count=10 -shuffle=on`、`make build`、`make dist`、驗證 `SHA256SUMS` 正好列出 4 個執行檔且雜湊正確、確認 Linux 執行檔是靜態連結且所有 dist 執行檔都是 `CGO_ENABLED=0`，再用 `scripts/ci-smoke.sh` 實際執行 `bin/nlf` 與本機平台的 dist 執行檔，最後確認工作目錄沒有被改動。

- 觸發條件：`pull_request`，以及推到 `main` 或 `feat/**` 的 `push`，且只在 `tools/nlf/**` 或 workflow 本身有變更時執行；也可以手動觸發（`workflow_dispatch`）。
- 最小權限：`permissions: contents: read`，checkout 不保留 credentials；不使用 secrets，不發布、不上傳任何東西。
- Go 版本取自 `go.mod`，並設定 `GOTOOLCHAIN=local`、`GOPROXY=off`，不下載任何模組或 toolchain。
- 第三方 action 只有 `actions/checkout` 與 `actions/setup-go`，都固定在 commit SHA（註解標明版本）。

在本機跑同樣的 smoke test（fixture 建在暫存目錄，不會刪除，結束時印出路徑）：

```
make build
v=$(sed -n 's/^const version = "\(.*\)"$/\1/p' main.go)
bash scripts/ci-smoke.sh bin/nlf $v
```
