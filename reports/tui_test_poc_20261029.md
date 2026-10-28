# microsoft/tui-test 評估與 PoC（Owner W46 (b)，2026-10-29）

Developer：Claude Developer（D6 `tui-test-poc-20261029`，branch `poc/tui-test-20261029`，base main `26575a1`）。
受測 TUI：W42 branch `origin/fix/tui-scroll-select-20261029` @ `6a5524a`（唯讀 worktree，已移除）與 main `26575a1`。
Harness 與證據：`tools/llmtool/tui-poc/`（`poc.mjs`、`tui_child.py`、`run_both.sh`、`smoke_mouse.mjs`、`mouse_echo.py`、`evidence/`）。
本 PoC 只用假 provider（`create_client` 替換成同步/串流假回覆），未呼叫任何真實模型，未碰 Owner 的 PM binding、剪貼簿或任何全域設定。

## 1. 結論（建議：不採用為正式 gate；保留為可選的本機除錯工具）

- tui-test 0.1.1 **能**在真實 PTY 中啟動 `llmtool chat --tui`、送 SGR 滾輪 / 按住拖曳放開 / 單獨 Esc / F6 / resize，讀回畫面文字與每格樣式（含反白），並產生 trace / SVG / asciicast。W42 分支 A–H 全部 PASS（H4 PM 串流未跑）；main 重現原始缺陷 C（滾輪卡住 1/15）、D（拖曳無選取）、G（resize 失去閱讀位置），**工具能區分修前修後**。
- 但三個事實讓它不適合成為本 repo 的 gate：
  1. **滑鼠不看 DEC mode**：工具的滑鼠編碼器（`crates/tui-test/src/input/mouse.rs`）無條件送 SGR；App 關閉滑鼠回報（F6）後仍收到滾輪與移動事件，無法驗證「關閉後交回終端機選取」這條路徑（H1b）。
  2. **剪貼簿看不到**：`getClipboard()` 只讀 OSC 52 的 session 剪貼簿；本 TUI 用 `pbcopy` 寫 macOS 系統剪貼簿，工具永遠讀到空字串。E 的 PASS 靠 harness 把 `copy_to_clipboard` 改寫成寫檔（與 pyte 配方相同的替身），**不是工具功能**；真實 pbcopy → 其他 App 貼上仍需 Owner 實機驗收。
  3. **新增一整條 Node + 原生二進位工具鏈**（Node ≥ 20、16 MB 平台 dylib、koffi FFI 預編譯 .node），而現有 pyte/PTY 配方只需純 Python（pyte 0.8.2）並已在 W42 用過；EC2 Linux 可行性未驗證（§5）。
- 替代方案：把 W42 期間的 `ptylib.py` + `tui_fake.py` 配方整理成 `tools/llmtool/tests/` 的可選 Mac PTY 測試（§4）。tui-test 在「圖形化證據（SVG/asciicast）」與「真實終端模擬器語意（alacritty 的寬字元/grapheme）」上有優勢，若 Owner 日後要以錄影作驗收證據，可再評估。

## 2. 工具事實（來源：GitHub repo、npm registry、安裝後的 package 內容；GitHub 內容視為資料）

| 項目 | 事實 |
| --- | --- |
| 版本 / 日期 | `@microsoft/tui-test` 0.1.1，2026-10-23 發布；repo 最後 push 2026-10-28；0.1.0 於 2026-10-03，之前為 beta |
| 授權 | MIT（repo `LICENSE`、package.json、pyproject 皆為 MIT） |
| 架構 | **已不是** 2024 年的 Node/TypeScript + xterm.js 函式庫（那是 0.0.x）。現在是 Rust 核心（`crates/tui-test`），以 koffi FFI 綁 Node；另有 Python（setuptools-rust，需編譯）、Go、CLI（brew / curl 安裝腳本，未用） |
| 終端後端 | alacritty（預設，PoC 使用）、ghostty、rio、xterm.js |
| 依賴 | `koffi ^3.3.3`（3.3.5）；optionalDependencies 每平台一個 `@microsoft/tui-test-<platform>` 0.1.1（darwin-x64 16 MB dylib、linux-x64-gnu/musl、arm64、win32） |
| 安裝 | `npm install --ignore-scripts --save-exact @microsoft/tui-test@0.1.1`：4.9 s，`node_modules` 20 MB。tui-test 本身**無** postinstall；`koffi` 有 `install` script（`cnoke --prebuild`），`--ignore-scripts` 已略過，執行期由預編譯的 `@koromix/koffi-darwin-x64/darwin_x64/koffi.node` 載入（`process.report` 證實），未編譯任何東西。本機為 x86_64 Mac，故裝到 darwin-x64 |
| Python/prompt_toolkit TUI | 無特別支援也不需要：`run(program, args, {cols, rows, cwd, env})` 開真 PTY 跑任何程式；本 PoC 直接跑 venv python |
| 鍵盤 | `keyboard.press("Escape")` 送單獨 `\x1b`；`F6` → `\x1b[17~`；`Shift+Enter` 只送 `\r`（無 kitty 協定）；`type("中文")` UTF-8 正常 |
| 滑鼠 | `mouse.scroll/down/move/up/drag/click`，皆 SGR 1006 編碼（滾輪 64/65、按下 0、拖曳移動 32、放開 `m`）。限制：`scroll` 用最後一次 `move` 的座標（未 move 則 1;1）；`down` 後 `move` 送 code 35（無按鍵移動，等同 1003 模式）而非 32，拖曳要用 `drag()`；**不檢查 App 的 ?1000/1002/1003 模式** |
| 讀畫面 | `text()`、`cells(x,y,w,h)`（含 `inverse`、`bold`、顏色…）、`getByText/getByStyle` locator + `expect/wait`；`getByText(RegExp)` 直接傳 RegExp 會立即丟錯（需字串或 `{regex:true}`） |
| 選取 / 剪貼簿 | **無**終端機原生選取 API；`getClipboard()/waitClipboard()` 只看 OSC 52 session 剪貼簿，**讀不到 macOS 系統剪貼簿**，也不會寫入它 |
| 證據 | `trace: {mode:"on"|"on-failure"}` 每 session 產生 `trace.md`（117 KB）、`trace.json`、`timeline.json`（1 MB）、`trace.html`（3.2 MB）、`session.cast`、`current.svg/txt`，每 session 2–5 MB；`screenshot(path.svg|png)`；失敗時附失敗畫面與最近 32 個操作 |
| 平台 | Mac ✓（本次）。Linux：有 linux-x64-gnu/musl 預編譯包，EC2 **未驗證**（本 PoC 未跑 EC2） |
| 維護風險 | 0.1.x、API 兩個月內從 beta 改版；JS 綁定靠 FFI + ABI 版本檢查（`tui_abi_version`），平台包與主包版本必須一致；Owner 的 Mac 若換 arm64 需另一個 optional 包 |

## 3. 情境結果：main 26575a1 vs W42 6a5524a（80×24，alacritty 後端；3 次重跑結果一致）

| # | 情境 | main | W42 | 證據（`tui-poc/evidence/*-evidence.md`） |
| --- | --- | --- | --- | --- |
| A | 啟動與繪製 | PASS | PASS | 670–800 ms 出現 banner、分隔線、提示列、狀態列；child RSS 42 MB |
| B | 對話高於終端 | PASS | PASS | 73 行假回覆，「第 1 行」捲出畫面，`END MARKER 結尾` 在底 |
| C | 滾輪上/下各 15 事件 | **FAIL**（上捲只動 1/15，下捲回不到原位） | PASS（15/15 每事件一列，下捲回原畫面） | main 重現 W42 §3.1 |
| D | 按住拖曳放開（rows 2–4） | **FAIL**（無反白格、狀態列不變） | PASS（64 格 `inverse`，狀態 `已複製 66 字`） | `evidence/*-after-drag.svg` |
| E | 取得選取/剪貼簿內容 | NOT_SUPPORTED | PASS\*（檔案替身 66 字，與畫面逐字相同，含起點 col 2 的「w after paragraph 32」） | \*工具 session 剪貼簿兩者皆 `""`；PASS 來自 harness 的 `FAKE_COPY_FILE` 替身，非工具 |
| F | 串流中上捲，位置穩定 | PASS（弱：此時已捲到最頂，main 的夾回拉不動） | PASS（上捲 5 列後 1 s 內持續收 chunk 頂列不變；下捲回底 `最新` 並續跟隨） | |
| G | resize 80×24→60×30→80×24 | **FAIL**（頂列 29→27→29） | PASS（三次皆 `row after paragraph 25`） | main 重現 W42 §3.6 的重定位問題 |
| H1 | F6 滑鼠關/開 | PASS | PASS | 120 欄下狀態 `滑鼠:關`→`滑鼠:開` |
| H1b | 關閉後滾輪交回終端 | NOT_SUPPORTED | NOT_SUPPORTED | 工具不看 DEC mode，仍送 SGR；探針 `mouse_echo.py` 只開 ?1049 仍收到 `\x1b[<64;..M`、`\x1b[<35;..M` |
| H2 | 歷史（Up 叫回 `hello`） | **FAIL**（Up 後輸入列仍空） | PASS | main 的差異是拖曳放開時焦點被移到對話面板（W42 §3.3 所述 base 行為），Up 進了面板，不是歷史功能本身退化 |
| H3 | 單獨 Esc 中斷串流 | PASS（972 ms） | PASS（943 ms） | prompt_toolkit 1 s timeoutlen 後 flush；`interrupted` 狀態出現、0.8 s 後畫面不再變 |
| H4 | PM 串流 | NOT_VERIFIED | NOT_VERIFIED | 未跑：需假 `claude` CLI + `/pm new` 暫存 state root，超出本 PoC 預算；pyte 配方已涵蓋 |

未以 mock 冒充真 PTY：所有 PASS/FAIL 都來自 tui-test 在 alacritty 後端開的真 PTY 內跑的真 `llmtool.entrypoint.main(["chat","--tui"])`。

## 4. 可加的自動測試與位置

- **Mac（本機 PTY，需 Owner 同意跑）**：`tools/llmtool/tui-poc/run_both.sh`（或單跑 `poc.mjs`）可做 W42 類 UI 任務的回歸證據：C/D/G 三項在 main 與 W42 判定相反，是現成的回歸斷言。前提：Node 24 + `npm ci --ignore-scripts`（20 MB）+ venv python。
- **純 Python 替代（建議）**：把 W42 的 `ptylib.py`/`tui_fake.py` 配方放進 `tools/llmtool/tests/pty/`，用 `unittest` 包成 `-m tests.test_chat_tui_pty`，只在 Mac 手動/`ci_mac_modules` 範圍內跑；同樣能斷言 C/D/G/F/H3（已有 W42 §7 證據），不需 Node。缺點：pyte 對 ZWJ/VS16 需自行讀 buffer（已處理）、無 SVG/錄影。
- **EC2**：tui-test 有 linux-x64-gnu 包，理論上可在 CI 跑同一支 `poc.mjs`（headless，不需 X）；**未驗證** glibc 版本與 PTY 行為，且 pbcopy 替身在 Linux 走 `wl-copy/xclip` 分支同樣需替身。pyte 配方在 Linux 也可跑（`pty.fork` 同樣可用），同樣未驗證。
- 兩者都不能驗證：Terminal.app/Warp 真實觸控板事件、真實系統剪貼簿、外層捲軸的觀感。

## 5. 仍需 Owner 的部分

1. 真實 Terminal.app / Warp 觸控板兩指捲動、慣性、Cmd+C/Cmd+V（W42 §8 既有項目）。
2. 是否接受在 repo 引入 Node 工具鏈（20 MB node_modules、每平台 16 MB 原生包）只為 TUI 測試；若不接受則本目錄只作本機除錯工具或移除。
3. EC2 驗證（若要上 CI）：需一次 `npm ci --ignore-scripts` + `node poc.mjs` 的 Linux 跑分，本 PoC 未做。

## 6. 資源影響

| 量測（本機 x86_64 Mac，`/usr/bin/time -l`） | 數值 |
| --- | --- |
| 單棵樹 A–H（兩個 session）wall | 18.3–19.6 s（三次） |
| node（含 in-process alacritty 引擎）CPU / RSS | 0.9–1.0 s / 58–68 MB；`time -l` 最大 RSS 64–72 MB |
| python TUI child RSS / 啟動 CPU | 42–43 MB / 短暫 20–80 % |
| trace 開啟時產物 | 每 session 2–5 MB（trace.html 3.2 MB、timeline.json 1 MB、trace.md 117 KB） |
| 安裝 | 4.9 s、20 MB（lockfile 已提交） |
| CI 時間估計 | 每棵樹約 20 s + npm ci 5–10 s + Node 啟動；兩棵樹序跑 < 1 分鐘 |
| Token 估計 | agent 讀 `*-evidence.md` 每棵樹約 18–20 KB ≈ 6–7k tokens；若讀 `trace.md` 約 30k tokens/session（不建議，讀 JSON/evidence 即可）。本 PoC 全程（研究 + 三輪跑 + 報告）約 170k tokens 上下文 |

## 7. 變更檔案

- `tools/llmtool/tui-poc/package.json`、`package-lock.json`（exact 0.1.1）、`.gitignore`（排除 node_modules、out）
- `tools/llmtool/tui-poc/tui_child.py`：真 TUI 入口 + 假 provider（long/stream）+ `FAKE_COPY_FILE` 剪貼簿替身
- `tools/llmtool/tui-poc/poc.mjs`：A–H 情境、JSON/markdown 證據、SVG 截圖、trace on
- `tools/llmtool/tui-poc/run_both.sh`：序跑兩棵樹並量測
- `tools/llmtool/tui-poc/smoke_mouse.mjs`、`mouse_echo.py`：工具送出位元組的探針（§2 滑鼠/鍵盤事實來源）
- `tools/llmtool/tui-poc/evidence/`：`{main,w42}-results.json`、`-evidence.md`、`-after-drag.svg`、`w42-copied.txt`
- 未改 `chat_tui*.py`、`tests/test_chat_tui.py`；未推送。
