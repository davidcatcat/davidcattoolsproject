# W42 TUI 捲動／選取／複製驗收失敗 — 修復報告（2026-10-29）

D6：`tui-scroll-select-20261029`，分支 `fix/tui-scroll-select-20261029`，base `main 0152c56`（含 #592 wheel/F6、#597 PM typewriter）。
範圍：TUI/UI/UX（`chat_tui.py`、新檔 `chat_tui_view.py`、`tests/test_chat_tui.py`、README TUI 段落）。未動 Core、dispatch、providers、runtime、GitHub sync、persistence、permissions。

## 1. 結論

六項問題的共同根因在 prompt_toolkit `TextArea`/`BufferControl` 的捲動模型：捲動由「游標所在邏輯行」驅動，`Window` 每次繪製都把視窗夾回游標可見處。折行後一個邏輯行等於多個螢幕列，滾輪一次移一個邏輯行、被夾回時整段跳動或完全不動；「跟隨最新輸出」又綁在同一個游標上。修法是把對話面板改為自有的**螢幕列模型**（`chat_tui_view.py`）：閱讀位置、跟隨狀態、選取範圍三者各自是明確狀態；滑鼠事件由 `ChatTUI` 自己解析（滾輪、拖曳、邊緣自動捲動、放開即複製到系統剪貼簿）。自動測試 149/149 通過；新回歸測試在 base 上全部失敗、修後通過；Owner 提供的模擬腳本在 base 與修後各跑一次的數字列於 §6；真實 PTY 操作列於 §7。只有 Mac GUI 才能看到的項目（觸控板、Cmd+C 貼到外部程式、內外雙捲軸、Warp）維持「尚未驗收」，§8 給 Owner 步驟。

## 2. 環境事實

| 項目 | 本次可取得 | 值 |
|---|---|---|
| 修復 base commit | 是 | `0152c56`（Owner 模擬用 `26575a1`，`chat_tui.py` 相同 blob `b471f93`） |
| macOS | 是 | 15.7.9 (24G830)，Darwin 24.6.0 |
| prompt_toolkit | 是 | D6 venv 3.0.52；Owner 端 3.0.53。兩版 `containers.py` 的 `_scroll_up/_scroll_down/_scroll_when_linewrapping` 相同，Owner 在 3.0.53/Linux 重現同樣缺陷 |
| Python | 是 | 3.13.3（venv）；Owner 模擬 3.12 |
| Claude Code | 是 | 2.1.312 |
| Terminal.app | PM 轉述 Owner | 2.14；另有 Warp 0.2026.10.21.08.25.02；直接在 Mac 執行，無 SSH、無 tmux |
| 本 session 的 TERM | 是 | `xterm-256color`（背景 shell，`TERM_PROGRAM` 空：非 GUI 終端） |
| 本次 PTY 證據的終端模擬器 | 是 | `pty.fork` + `pyte` 0.8.2（VT100/xterm 模擬，不是 Terminal.app 的 GUI） |

無法自行取得、無需再問：Owner 已回覆終端版本與執行方式。仍需 Owner 實機操作的項目見 §8。

## 3. 六項問題：重現、根因、責任歸屬、修法

責任歸屬：**app** = 本專案 `chat_tui.py`；**ptk** = prompt_toolkit 的設計；**terminal** = 終端模擬器行為。

### 3.1 上下捲動卡住（app + ptk）
- 重現（base，PTY 80×24，含折行中文段落的回覆）：`repro_before.py` 15 次 SGR 滾輪上（`\x1b[<64;10;5M`）只移動 1 列，其餘 14 次畫面不變；另一輪直接跳到最頂後 15 次滾輪下全部無效。Owner 模擬：120 行折行中英文，12 次滾輪上 `vertical_scroll` 停在 121。
- 根因：
  - `prompt_toolkit/layout/containers.py` `Window._scroll_down`：`if self.vertical_scroll < info.content_height - info.window_height` 用**邏輯行數**減**螢幕列數**，折行內容下幾個長行時恆為 False，滾輪下無效。
  - `Window._scroll_up`：只有游標在視窗最後一列時才把游標上移，否則只減 `vertical_scroll`；接著 `_scroll_when_linewrapping`（同檔）為了讓游標可見再把 `vertical_scroll` 夾回去 → 畫面不動（原始碼內即有 `TODO: not entirely correct yet in case of line wrapping and long lines`）。
  - `chat_tui.py`（base）`scroll()` 用 `get_cursor_up_position(count=rows)` 以邏輯行數當列數；`_follow_tick()` 用同一個錯誤比較判定「到底」。
- 修法：`chat_tui_view.ConversationView` 自行折行成列（`get_cwidth`，CJK/emoji 2 欄、tab 4 欄），`top` 即第一可見列；`Window` 以 `wrap_lines=False` + `get_vertical_scroll=lambda: view.top` 顯示，游標隱藏且固定在 `top` 列，因此 `Window` 的夾回結果就是 `top`。滾輪每事件 `WHEEL_ROWS = 1` 列；PageUp/Down 一屏；對話有焦點時 Up/Down 一列、Home/End 到頂/底。
- 證據：`ScrollSelectTests.test_wheel_up_moves_wrapped_cjk_lines_one_row_per_event_and_back`（base FAIL：第 1 次滾輪後第一列不是上一列；修後 PASS，12 上 12 下每次恰移一列並回到原畫面）；PTY `pty_after.py`：30 上 30 下 60/60 事件各移一列（§7）。

### 3.2 TUI 與外層 Terminal 各一條捲軸（app；外層屬 terminal）
- 根因：base `TextArea(scrollbar=True)` 在面板右側畫 `ScrollbarMargin`；Terminal.app 自己的視窗捲軸在 alternate screen（`\x1b[?1049h`）內無法由程式移除，兩條並存且外層那條無法捲動 TUI。
- 修法：移除面板捲軸；位置改由狀態列呈現：`最新`（跟隨中）、`捲動 63%`、`捲動 40% ⇣新輸出`（上捲時有新輸出）、`已選 N 字`、`已複製 N 字`。
- 殘留：外層 Terminal 捲軸仍在（alternate screen 的固有現象，與 vim/less 相同）；只有改成不用 alternate screen 的 inline 繪製才會消失（§5）。「混淆感」需 Owner 實機判斷。

### 3.3 按住左鍵拖曳時無法繼續往上捲以擴大選取（app + ptk）
- 根因：base 的選取完全交給 `BufferControl.mouse_handler`（`controls.py` 824–901）：只在該控制項**已有焦點**時處理拖曳（第一次拖曳只在放開時取得焦點，不產生選取——Owner 報告第 4 點），沒有邊緣自動捲動，滾輪則經 §3.1 的壞路徑。
- 修法：`ChatTUI.mouse_event` 以 app 層 `Keys.Vt100MouseEvent` 綁定接管所有滑鼠事件（`_pane_mouse`/`_drag_to`）：面板上左鍵按下即 `begin_drag`（不需先點擊取得焦點、也不移動輸入框焦點）；拖曳到面板第一列/最後一列或超出面板時每事件捲一列，並以 `asyncio` `call_later(0.05 s)` 計時器在指標停住時持續捲動與擴大選取；放開時 `end_drag`。另外在開啟滑鼠回報時補送 `\x1b[?1002h`（xterm button-event tracking；`_enable_mouse_drag_reporting`），確保只支援 1002 而非 1003 的終端也回報按住拖曳的移動事件，且關閉時一併 `?1002l`。
- 證據：`test_drag_to_the_top_edge_keeps_scrolling_and_extends_the_selection`（base FAIL：第一次拖曳 0 個選取格；修後 PASS：停住 0.4 s 至少再捲 3 列，選取起點在到達的頂列第 4 欄，行數 = 按下列−頂列+1）；Owner 模擬 `edge_before_hold→edge_after_hold`：base 92→92 / 122→122，修後 69→55 / 549→535；PTY `pty_select.py` S2：到頂邊後停 0.6 s 第一列由「row after paragraph 27」變「15」，放開後複製 885 字 28 行。

### 3.4 反白後 Cmd+C 貼上得到其他內容（terminal + app）
- 根因：Terminal.app 不會把 Cmd+C 傳給程式，它只複製**終端自己的選取**；滑鼠回報開啟時終端沒有選取（或留著更早的舊選取），所以 Cmd+C 複製到的是舊剪貼簿或舊選取區的目前畫面內容。base 的 app 內選取（`BufferControl`）只存在 prompt_toolkit 的 `InMemoryClipboard`（Owner 模擬：放開後仍為空字串），從未到系統剪貼簿；Terminal.app 也不支援 OSC 52（網路來源，§9），程式無法以逸出序列寫剪貼簿。
- 修法：放開滑鼠時以文字 offset 取得選取內容（與折行無關，CJK/英文/跨頁一致），呼叫 `pbcopy`（macOS；其他平台 `wl-copy`/`xclip`/`xsel`），狀態列顯示 `已複製 N 字`；之後 Cmd+V 即貼出該內容。Ctrl-C 在有選取時改為再複製一次並清除選取（避免 Warp 若把無選取的 Cmd+C 當 ^C 送入時誤觸中斷/離開），無選取時行為不變。雙擊選取一個字詞。
- 證據：`ConversationViewTests.test_selection_offsets_give_exactly_the_selected_text`、`test_clipboard_tool_is_called_with_the_exact_text`（`subprocess.run(['pbcopy'], input=UTF-8 bytes)`）；PTY `pty_select.py` S1 複製 127 個中文字，內容與面板文字逐字相同（用檔案替代 pbcopy，見 §7 說明）。**真實 pbcopy → 其他 App 貼上** 尚未驗收（§8）。

### 3.5 觸控板兩指捲動只動一點點（app + terminal）
- 根因：Terminal.app 在 alternate screen + 滑鼠回報下把兩指捲動轉成逐列的滾輪事件；base 每事件移一個邏輯行且多半被 §3.1 夾回，所以只動一點甚至不動。
- 修法：同 §3.1，每事件一列，連續事件連續位移，無夾回。
- 證據：PTY 連續 30 事件 30 列；真實觸控板的事件數量、慣性、反向由 Terminal.app/Warp 產生，**尚未驗收**（§8）。

### 3.6 滾輪亂跳（app + ptk）
- 根因：§3.1 的游標夾回：游標在長折行上時 `_scroll_when_linewrapping` 把 `vertical_scroll` 設回游標行，畫面整段跳到游標行；`append()`/`_block_render()` 以「游標在文末」判定跟隨，游標沒動時新輸出把畫面拉回底部（Owner 的「莫名回到底部」）。
- 修法：跟隨是明確的 `following` 旗標，只有視窗到達底部才為真；新輸出來時 `top` 不變；resize/重折行時以 `top_offset`（頂列文字 offset）重新定位。
- 證據：`test_short_lines_scroll_one_row_per_wheel_event_and_keep_position_on_append`、`test_drag_below_the_pane_scrolls_down_and_selection_survives_streaming`（含 resize 80→45 欄後頂列文字相同）；PTY `pty_after.py` B/C 與 `pty_stream.py` E2/E3（串流中上捲後頂列 1 s 不變、捲回底部恢復 `最新`）。

## 4. 為何新增 `chat_tui_view.py`，而不是 prompt_toolkit 現成功能

檢查過的 prompt_toolkit 3.0.52/3.0.53 功能與不足：

| 功能 | 不足 |
|---|---|
| `TextArea(scrollbar, wrap_lines)` / `BufferControl` | 捲動 = 移動游標；`Window._scroll_up/_scroll_down` 以邏輯行為單位且與 `window_height` 混用；`_scroll_when_linewrapping` 每次繪製夾回游標行（§3.1、3.6）。沒有「閱讀位置獨立於游標」的概念 |
| `key_binding/bindings/scroll.py`（`scroll_page_up/down`、`scroll_one_line_*`） | 同樣以 `buffer.cursor_position` 與 `get_height_for_line` 推算，仍是游標模型 |
| `Window(get_vertical_scroll=…, allow_scroll_beyond_bottom)` | 只接受「偏好值」，繪製時仍以游標可見為準；只有在 `wrap_lines=False` 且游標列 = 偏好值時結果才等於偏好值——這正是本修法利用的方式（視圖自己折行，交給 `Window` 不折行的列） |
| `BufferControl.mouse_handler` 的拖曳選取（`controls.py` 846–869） | 需先有焦點；無邊緣自動捲動；選取存在 `Buffer.selection_state`，`set_document()` 新文件即歸零（Owner 第 3 點）；複製只到 `app.clipboard`（`InMemoryClipboard`） |
| `Output.enable_mouse_support` | 只送 1000/1003/1015/1006，無 1002；補送由 app 層包裝 |
| `ScrollablePane` | 給多個子容器用，內部仍靠子控制項游標決定捲動，不處理文字選取 |
| `Buffer.selection_state` + `PasteMode` | 剪貼簿是程式內物件，無系統剪貼簿整合；Terminal.app 無 OSC 52 |

因此新模組承擔的是 prompt_toolkit **沒有**提供的三件事：以列為單位的閱讀位置/跟隨狀態、以文字 offset 表示並能跨頁擴張的選取、系統剪貼簿。它不重作繪製、鍵盤、佈局、輸入框或事件迴圈，仍用 `Window`/`UIControl`/`UIContent`/鍵綁定，366 行（含 docstring），純 Python 部分可離線單元測試。不建議 fold back：fold back 等於回到游標模型，即 §3 的根因。

## 5. 與 Claude Code 2.1.312 的比較

實測（`claude_probe.py`：在 PTY 啟動 `claude` 6 秒並擷取 DEC private mode）：Claude Code 只設 `?1004h`（focus events）、`?2004h`（bracketed paste）、`?2031h`（theme）與游標顯示/隱藏；**沒有** `?1049h`（alternate screen）、**沒有** 任何 `?1000–1006h`（滑鼠回報）。它把對話逐段印進一般緩衝區、只重繪底部的活動區，所以捲動、選取、Cmd+C、觸控板、捲軸全部是 Terminal.app/Warp 原生行為，程式完全不碰滑鼠；代價是歷史內容印出後不能再改（串流中的區塊要到完成才「落地」）、沒有程式內的 PageUp/搜尋、無法在程式內標示選取狀態。

本專案 TUI 的模型（alternate screen + 滑鼠回報 + 自有列模型）與 vim/less/tmux 相同：程式擁有捲動與選取，可在歷史文字上就地重繪（PM typewriter、串流中斷標記、Task 面板都依賴這點）。兩種設計都合理；差別是「誰擁有滑鼠」。Owner 的六項需求在修後都能在本模型內滿足，剩下的差異是外層捲軸與觸控板手感（§8）。若 Owner 實機驗收後仍認為外層捲軸/原生手感是硬需求，替代方案是 inline 繪製（`full_screen=False`、不開滑鼠回報、完成的區塊以 `print_formatted_text` 印入 scrollback，只重繪輸入框與活動串流區）：估計 `chat_tui.py` 面板/串流/Task 面板約 600 行需重寫，F1/F2/F3/F4 面板要改為臨時覆蓋或另開方式，工時約 2–3 個 D6；本次未做，也不建議在未實機驗收前做。

## 6. 自動測試證據（base vs 修後）

執行：`tools/llmtool/.venv/bin/python -m unittest tests.test_chat_tui`（僅此模組；Mac 與 CI 共用，未跑全套）。修後 **149 tests OK**（含既有 PM typewriter `PMTypewriterTests`、`PMConversationTests`、Esc/中斷、F6、history 測試）。

同一份測試檔對 base `chat_tui.py` 執行（`tmp/base_pkg`：套件複本 + `git show 0152c56:…/chat_tui.py`）：

| 回歸測試（`ScrollSelectTests`） | base 0152c56 | 修後 |
|---|---|---|
| `test_wheel_up_moves_wrapped_cjk_lines_one_row_per_event_and_back`（Owner 第 1 點） | FAIL：第 1 次滾輪後第一列不是原列（畫面整段跳） | PASS |
| `test_short_lines_scroll_one_row_per_wheel_event_and_keep_position_on_append` | FAIL：無 `⇣新輸出` 狀態（跟隨/閱讀位置未分離） | PASS |
| `test_drag_to_the_top_edge_keeps_scrolling_and_extends_the_selection`（第 2 點） | FAIL：第一次拖曳 0 個反白格 | PASS |
| `test_drag_below_the_pane_scrolls_down_and_selection_survives_streaming`（第 3 點 + resize） | FAIL：0 個反白格（未聚焦的首次拖曳無選取） | PASS |
| `test_pageup_pagedown_arrows_and_home_end_scroll_rows` | ERROR：無 `view` | PASS |
| `test_ctrl_c_with_a_selection_copies_and_clears_instead_of_exiting` | ERROR | PASS |
| `test_f6_hands_the_mouse_to_the_terminal_and_back` | FAIL（訊息文字） | PASS |
| `test_mouse_modes_include_button_event_tracking` | ERROR：無 1002 包裝 | PASS |
| `test_clicks_on_the_pane_keep_input_focus_and_wheel_never_recalls_history` | FAIL：點擊面板把焦點移走 | PASS |
| `ConversationViewTests`（8 個：CJK 折行寬度、跟隨狀態、resize 錨定、選取=offset、雙擊、複製失敗回報、pbcopy 參數、SGR/X10 解析） | n/a（新模組） | PASS |

Owner 模擬腳本 `probe/simulate.py`（pipe 路徑；快照函式改為兩種模型都能讀，事件序列不變）：

| 情境 | base（caret 模型） | 修後（rows 模型） |
|---|---|---|
| short：12 次滾輪上 | 104→92（每次 −1） | 106→94（每次 −1） |
| wrapped：12 次滾輪上 | 121 全程不動，`_view_at_bottom` 仍 True | 586→574（每次 −1），`following` False |
| 上捲後 append | 92→92 / 121→122 | 94→94 / 574→574 |
| 頂邊拖曳 + 停 0.7 s | 92→92 / 122→122 | 69→55 / 549→535 |
| 放開後選取字數 | 255 / 1342 | 1050 / 3878（copier 收到同樣文字） |
| 一次 `_block_render()` 後選取 | 0 / 0（清除） | 1050 / 3878（保留） |
| resize 80→45 欄 | 不崩潰 | 不崩潰，頂列文字錨定 |

## 7. Mac PTY 實機操作證據（修後）

方式：Mac 上 `pty.fork()` 執行真正的 `llmtool chat --tui` 入口（`tui_fake.py`：只替換 `llmtool.chat.create_client` 為假 provider，`HOME` 指向暫存設定；`FAKE_COPY_FILE` 把 `chat_tui_view.copy_to_clipboard` 改寫成寫檔，**刻意不碰 Owner 的真實剪貼簿**），輸出餵給 `pyte` 取畫面。腳本與 log 在 job tmp（`pty_after.py/.log`、`pty_select.py/.log`、`pty_stream.py/.log`），未提交。

- 終端模式：`?1049 ?1000 ?1002 ?1003 ?1006 ?1015 ?2004`（1002 為本次新增）。
- 滾輪：折行中文回覆底部起，30 上/30 下共 60 事件 **60 次都恰移一列**；狀態 `最新`→`捲動 63%`→`最新`。
- 閱讀位置：上捲 10 列後送第二題，整個面板畫面與送前相同，狀態 `捲動 40% ⇣新輸出`；捲回底部後 `最新`、顯示 `END MARKER 結尾`。
- resize：80×24→60×30→80×24，頂列文字「row after paragraph 5」三次相同。
- 選取/複製：S1 拖曳 13→16 列放開，複製 127 字中文，與畫面逐字相同，狀態 `已選 127 字`→`已複製 127 字`；S2 拖到頂邊停 0.6 s 自動上捲 12 列，放開複製 885 字 28 行；S3 新回覆到達時頂列不變且選取仍在；S4 有選取時 Ctrl-C 再複製一次、程式續跑；S5 雙擊複製 `after` 一字詞。
- 串流（假串流 provider 400 詞）：貼上兩行（bracketed paste）後送出，面板顯示兩行；串流中上捲 5 列，之後 1 s 內持續收到 chunk 頂列不變（`捲動 0% ⇣新輸出`）；捲回底部後 `最新` 且後續 chunk 持續跟隨；Esc 中斷顯示 `reply interrupted after the text above`；第二輪第一個 Ctrl-C（無選取）中斷。

## 8. 三欄驗收表

| 驗收項目 | 自動測試 | Mac PTY | Owner 實機（尚未驗收） |
|---|---|---|---|
| 折行中英文連續滾輪上下、每事件一列、不跳不卡 | PASS | PASS（60/60） | 尚未驗收：Terminal.app 2.14 與 Warp 實際滾輪 |
| 觸控板慢/快/慣性/反向穩定 | n/a | n/a | 尚未驗收 |
| 串流中閱讀位置不動、回底部恢復跟隨 | PASS | PASS | 尚未驗收（Owner 用真 provider 看一次） |
| 拖到邊緣持續捲動並擴大選取 | PASS | PASS | 尚未驗收：GUI 拖曳（含指標移出視窗時 Terminal.app/Warp 是否仍回報） |
| 複製 = 選取（CJK/英文/折行/跨頁） | PASS（offset 層與 pbcopy 參數） | PASS（寫檔替代 pbcopy） | 尚未驗收：真 pbcopy → 其他 App Cmd+V |
| resize／焦點切換／重繪不錯位 | PASS | PASS | 尚未驗收：視覺觀感 |
| 輸入、多行貼上、送出、中斷無回歸 | PASS（既有測試） | PASS | — |
| PM 即時串流 typewriter（#597）、Esc、F6、history | PASS（既有測試） | Esc/F6 PASS；PM typewriter 未在 PTY 跑（需真 claude） | 尚未驗收：`/pm connect` 一輪 |
| 內外雙捲軸不再混淆 | PASS（內層已移除） | PASS（畫面無 `^`/`v` 欄） | 尚未驗收：外層 Terminal 捲軸仍在，是否可接受 |
| Warp | n/a | n/a | 尚未驗收：Warp 的滑鼠回報、Shift-drag、Cmd+C 行為 |

Owner 步驟（Terminal.app 2.14，再在 Warp 重做一次）：
1. `git fetch && git checkout fix/tui-scroll-select-20261029`（PM 開 PR 後），`llmtool chat --tui`，問一個會回長段落中文的問題，等回覆完成。
2. 兩指慢慢往上捲、再快捲、放手看慣性；再滾輪上下各 20 格。預期：每格移一列、不跳、不卡；狀態列由 `最新` 變 `捲動 N%`，捲回底部變 `最新`。
3. 往上捲一半後再送一題：預期畫面不動，狀態 `捲動 N% ⇣新輸出`；捲到底後跟著新輸出。
4. 在回覆中按住左鍵拖曳到視窗最上一列不放：預期持續往上捲且反白持續擴大；放開後狀態列 `已複製 N 字`。到另一個 App（備忘錄/編輯器）Cmd+V：預期貼出的文字與反白完全相同（含中文與換行）。注意：**不必**再按 Cmd+C；若按了，Terminal.app 應無反應（它沒有選取）。
5. 雙擊一個英文字：預期該字反白並 `已複製`。
6. 縮放視窗寬度：預期頂列文字不變、反白仍在原文字上。
7. 若仍想用 Terminal.app 原生選取：按住 Fn 拖曳（Warp：Shift 拖曳），或 F6 關閉滑鼠回報。
8. 回報時附：Terminal.app/Warp 版本、第 2 步有無跳動（說明跳到哪）、第 4 步貼出的文字是否相同。

## 9. 殘留與未處理

- Help（F1）、Task detail（F2）、events（F4）面板仍是 `TextArea(scrollbar=True)`：同樣的游標模型與捲軸；本次未改（不在 Owner 六項內）。若要一致可將 `make_pane` 套用到這三個面板（估 1 個小 D6）。
- 外層 Terminal 捲軸（alternate screen 固有）見 §3.2、§5。
- Warp 的 Cmd+C 無選取時是否送 ^C：網路來源未證實；已以「Ctrl-C 有選取時不離開」防護。
- 1002/1003 在 Terminal.app 2.14 的實際支援未由本機證實（網路來源：tmux 拖曳在 Terminal.app 可用）；若 Owner 第 4 步完全沒有反白，代表終端未回報拖曳事件，請回報。
- 網路來源（僅供佐證，非本機驗證）：Terminal.app 以 Fn 拖曳繞過滑鼠回報、不支援 OSC 52；Warp 以 Shift 拖曳繞過、Cmd-R 切換滑鼠回報。

## 10. 變更檔案

- `tools/llmtool/llmtool/chat_tui_view.py`（新）：`Wrapper`、`ConversationView`、`parse_mouse`、`copy_to_clipboard`、`make_pane`。
- `tools/llmtool/llmtool/chat_tui.py`：對話面板改用 view；`mouse_event/_pane_mouse/_drag_to/_autoscroll_*`；`_enable_mouse_drag_reporting`；PageUp/Down、Up/Down、Home/End；Ctrl-C 有選取時複製；狀態列 `scroll` 欄；KEY_HELP。移除 `_follow_tick`、游標跟隨判定與面板捲軸。
- `tools/llmtool/tests/test_chat_tui.py`：`ConversationViewTests`（8）、`ScrollSelectTests`（9），取代 3 個游標模型測試，更新 2 個狀態列字串。
- `tools/llmtool/README.md` TUI 段落。
- 本報告。

## 11. Codex Review v1（PR #606）兩項 P2 的處理

1. **剪貼簿無上限且阻塞 UI 執行緒**（`chat_tui_view.py` `copy_to_clipboard`）。修法：`ConversationView.copy()` 先算 UTF-8 位元組數，超過 `COPY_LIMIT_BYTES`（1 MiB）直接拒絕並在狀態列顯示 `選取 N KB 超過複製上限 1024 KB`；未超過時在 daemon 執行緒 `llmtool-clipboard` 執行 pbcopy（原有 3 s timeout），UI 迴圈不等待，狀態列期間顯示 `複製中…`，完成後經 `app.invalidate`（thread-safe）更新為 `已複製 N 字`／`複製失敗`。`wait_copy()` 供測試 join。測試：`test_copy_runs_off_the_ui_thread_and_refuses_oversized_selections`（慢 copier 期間 `end_drag` 立即返回、狀態 `複製中…`、執行緒名稱；上限 8 bytes 時 12 bytes 的選取被拒、6 bytes 的照常複製）。
2. **折行以 code point 計寬，ZWJ 表情、旗幟、組合字可能被拆開**（`Wrapper.line_rows`）。修法：以 `wcwidth.iter_graphemes`（wcwidth 0.9.2，prompt_toolkit 既有依賴，已在 `worktree-env.lock`；舊版 wcwidth 無此函式時退回模組內的保守叢集規則：組合字、ZWJ 及其後字、變體選擇子、膚色修飾、tag、區域指示字成對）切成字素叢集，列只在叢集邊界斷開；`offset_at` 把落在叢集內的欄位對應到叢集起點，選取永不切半。**寬度刻意用 prompt_toolkit 渲染器的算法**（逐 code point `get_cwidth` 相加，例如 👨‍👩‍👧 = 2+0+2+0+2 = 6 格、🇹🇼 = 4 格），而不是 `wcswidth` 的 2 格：渲染器是逐 code point 擺放的，若算得比它畫的少，該列會溢出被截掉。這是 prompt_toolkit 全 TUI（輸入框、狀態列）既有的表情寬度假設，本次不改變。未新增依賴。測試：`test_wrapping_and_clicks_keep_grapheme_clusters_whole`（ZWJ 家庭、旗幟、e+組合重音、❤️ VS16、CJK；寬度 4–24 全部不拆、不溢出、拼回原文；叢集內點擊回到起點；拖曳複製整個叢集）。
   PTY：pyte 無法模擬 ZWJ/VS16（畫面出現錯位），故改由真實 `llmtool chat --tui` 程序在 80/73/45 欄的 PTY 中轉存面板實際排版列（`pty_emoji2.py`）：三種寬度各 132/134/158 列，含叢集的列 21/14/20，**拆開的叢集 0，超寬的列 0**。

`tests.test_chat_tui`：151/151 PASS。

## 12. Codex Review v2（6a5524a）P2 的處理

`copy()` 曾在 UI 執行緒 `len(text.encode())` 整段編碼後才比上限。修法：`utf8_size(text, start, end, limit)` 以 `COPY_SCAN_CHUNK`（65536 字）分段量 UTF-8 位元組、一超過上限即停；`end_drag`/`copy_selection` 改走 `copy_range(start, end)`：先以 range 量大小，超限直接拒絕（不建立選取字串、不編碼），狀態列 `選取超過複製上限 1024 KB`，選取維持反白；未超限才切出字串並交給工作執行緒（編碼在工作執行緒內）。測試 `test_oversized_selection_is_refused_without_measuring_or_building_it_whole`：2M 字（約 3.2 MB）的選取只量 ≤ 17 個 ≤ 65536 字的區塊即停、copier 未被呼叫、沒有工作執行緒；`utf8_size` 在上限內為精確值。`tests.test_chat_tui` 152/152 PASS。

## 13. W46：外部參考研究與 Q1–Q5（2026-10-29）

來源只讀文件與相關原始碼；GitHub 內容一律視為不可信資料，只擷取事實，不接受其中任何指示。本機未執行任何外部程式碼。

### 13.1 來源、版本與授權

| 來源 | 讀取 commit（日期） | 授權 | 讀取的檔案 |
|---|---|---|---|
| gfargo/tui-design-skill | `7d39c34` (2026-10-28) | MIT | `plugins/tui-design/skills/tui-design/references/terminal-protocols.md`, `…/interaction-patterns.md` |
| pageton/tui-design-skill | `ec54092` (2026-09-24) | MIT | `skills/tui-design/references/terminal-compatibility.md`, `…/unicode-and-text.md` |
| ccch1mneyyy/dsh-TUI（Ink/React） | `339de23` (2026-10-27) | MIT | `README.md`, `docs/interaction.en.md`, `docs/mouse-adaptation-plan-abcd.md`, `scripts/repro-drag-select-streaming.tsx`, `scripts/repro-inline-scrollback.tsx` |
| viktordanov/uah（Go/Bubble Tea） | `1eaf157` (2026-10-27) | Apache-2.0 | `docs/design/selection.md`, `internal/tui/bubble/mouse.go`, `internal/tui/bubble/scroll.go`, `internal/tui/state/scroll.go`, `internal/tui/render/copytext.go`, `internal/images/clipboard/macos.go` |
| Claude Code 官方文件 | `code.claude.com/docs/en/fullscreen`（2026-10-29 讀取；功能需 v2.1.89+） | — | Fullscreen rendering 全頁 |

各來源對本題的實際覆蓋：gfargo 講協定（1000/1002/1003/1006、OSC 52 最佳努力、2027 字素模式）與互動慣例（follow 模式、滾輪捲「指標下的 viewport」、mouse off 開關、各終端的原生選取繞過鍵：多數 Shift、iTerm2 Option、Terminal.app Fn）；pageton 講終端相容矩陣（不含 Terminal.app/Warp）與寬度量測（依顯示寬度、不拆字素）；dsh-TUI 是 Ink 應用，inline 模式「不開滑鼠回報、由終端原生選取」，fullscreen（alt screen）才有拖選、放開即複製、雙/三擊、OSC 52 優先＋本機工具退路，並有兩支 PTY（xterm headless）重現腳本：串流中拖選失效、inline 模式 scrollback 汙染；uah 是 Go 應用，`docs/design/selection.md` 是與本題最接近的設計文件：滑鼠預設開、放開即複製並顯示 toast、頂/底邊 50 ms tick 自動捲動（每 tick 最多 8 行）、拖曳中 view 釘住（pinned）不被串流推動、`NewBelow` 新輸出指示、OSC 52 與 pbcopy 雙寫且都在 update loop 之外、`state.MousePress/MouseDrag/MouseRelease` 純 reducer、測試注入 `CopyText` 不碰真實剪貼簿。Claude Code 官方文件：fullscreen 模式畫在 alternate screen、擷取滑鼠、程式內選取、「Selected text copies to your clipboard automatically on mouse release」、本機 macOS 走 `pbcopy`、SSH 才退 OSC 52、Terminal.app 原生選取按 `Fn`、「If you have a selection active, `Ctrl+c` copies instead of cancelling」、Esc 保留選取、上捲暫停 auto-follow 並顯示 Jump to bottom／`3 new messages`、滾輪「a few lines at a time」加 `CLAUDE_CODE_SCROLL_SPEED` 倍率與加速、`CLAUDE_CODE_DISABLE_MOUSE` 保留原生選取、iTerm2 需開 mouse reporting。

### 13.2 Q1 雙捲軸、亂跳、拖曳卡住、複製錯誤的根因（對應本專案檔案）

- **雙捲軸**：內層是 base `chat_tui.py` `TextArea(scrollbar=True)` 的 `ScrollbarMargin`；外層是 Terminal.app 在 alternate screen（prompt_toolkit `Application(full_screen=True)` → `vt100.py` `enter_alternate_screen` 送 `?1049h`）仍顯示的自身捲軸。外層在 alt screen 內無內容可捲（Claude Code 文件同樣指出 fullscreen 下「conversation lives in the alternate screen buffer instead of your terminal's scrollback」）。修後內層已移除；外層是 alt screen 固有。
- **亂跳／卡住**：prompt_toolkit `layout/containers.py` `Window._scroll_down`（邏輯行數 vs 列數的比較）、`_scroll_up`（只有游標在最後一列才上移游標）、`_scroll_when_linewrapping`（每次繪製把 `vertical_scroll` 夾回游標行）；base `chat_tui.py` `scroll()` 以邏輯行當列、`_follow_tick()` 用同一錯誤比較。gfargo 的互動慣例（滾輪捲 viewport 而非 selection；follow 暫停後「holds the viewport still」）與 uah 的 pinned 模型都把「閱讀位置」當獨立狀態，而 prompt_toolkit 把它綁在游標上，這正是差異所在。
- **拖曳卡住**：base 完全交給 `BufferControl.mouse_handler`（`controls.py` 824–901）：需先有焦點（第一次拖曳只在放開時聚焦）、無邊緣自動捲動；uah 與 Claude Code 都以 50 ms tick 自動捲動並擴選。
- **複製錯誤**：Terminal.app 不把 Cmd+C 交給程式、不支援 OSC 52（gfargo：OSC 52「Most terminals; some disable it」、「treat it as best effort」；pageton 相容矩陣未列 Terminal.app；Claude Code 文件本機用 `pbcopy`）；base 的程式內選取只到 prompt_toolkit `InMemoryClipboard`，從未到系統剪貼簿，Cmd+C 複製到的是終端舊選取或舊剪貼簿。dsh-TUI 另記錄一個同類坑：右側 2 欄 gutter 標成 `NoSelect fromLeftEdge` 讓整個 transcript 不可選（本專案已無 gutter，不適用）。

### 13.3 Q2 是否重做 prompt_toolkit／終端已有的功能

- 終端功能沒有被重做：滾輪、拖曳、座標都來自終端的 SGR 1006 回報（prompt_toolkit 解析表 `key_binding/bindings/mouse.py`，本專案 `parse_mouse` 直接重用其 `xterm_sgr_mouse_events`/`typical_mouse_events`）；剪貼簿交給 `pbcopy`；原生選取繞過（Terminal.app Fn／Warp Shift）保留。
- prompt_toolkit 已有而且仍在用的：`Window`/`UIControl`/`UIContent` 繪製與 diff 輸出、鍵綁定、`get_cwidth`、`wcwidth`（含 `iter_graphemes`）、`Application` 事件迴圈、`TextArea` 輸入框、1000/1003/1006/1015 的開關（本專案只補 1002）。
- prompt_toolkit 沒有、所以必須自寫的三件事（§4 表）：以列為單位且獨立於游標的閱讀位置與 follow 狀態；以文字 offset 表示、可跨頁擴張、串流重繪後仍在的選取；系統剪貼簿。這與 uah 自寫的 `state/scroll.go`（釘住、`NewBelow`）、`state/selection.go`、`render/copytext.go`、`bubble/mouse.go`（edge tick）是同一組東西——Bubble Tea 同樣沒有提供，uah 用約 26 KB Go 實作，本專案 `chat_tui_view.py` 約 400 行 Python，比例相當。
- 可被現成機制取代的部分：無。曾評估 `Window(get_vertical_scroll=…)`（只是偏好值，繪製仍以游標可見為準；本修法正是靠「視圖自行折行＋不折行的列＋游標釘在 top」讓它的夾回結果等於 top）、`ScrollablePane`（仍靠子控制項游標）、`Buffer.selection_state`＋`InMemoryClipboard`（無系統剪貼簿、`set_document` 即清除）。

### 13.4 Q3 ~600 行是否必要；有沒有更簡單、更低風險的設計

對照三種設計：

| 設計 | 捲動／選取／複製由誰負責 | 本專案代價 | 風險／證據 |
|---|---|---|---|
| A. 現行：alt screen＋滑鼠回報＋自有列模型（＝Claude Code fullscreen、uah、dsh-TUI fullscreen） | 程式；放開即 pbcopy | 已完成：`chat_tui_view.py` 約 400 行＋`chat_tui.py` 約 150 行差異，152 測試 | 外層捲軸無法消除；觸控板手感取決於終端送出的事件數（Claude Code 為此提供 `CLAUDE_CODE_SCROLL_SPEED` 倍率與加速；本專案可加同類設定） |
| B. inline／normal buffer（＝Claude Code classic、dsh-TUI inline、gfargo「inline tools leave the mouse off」） | 終端原生：捲動、選取、Cmd+C、觸控板、單一捲軸全部天然正確 | 估 2–3 個 D6：對話改為「完成區塊印入 scrollback、只重繪輸入框與串流中的活動區」；F1/F2/F3/F4 面板、PM typewriter 就地重繪、串流中斷標記、Task 面板都要改成臨時覆蓋或放棄就地修改 | dsh-TUI `repro-inline-scrollback.tsx` 記錄的整類 bug：增量重繪的擦除行數與前一幀不符、或幀高於 viewport 時，舊內容「永久落入 scrollback」（重複 UI、亂序）；Claude Code 官方文件說明他們做 fullscreen 正是因為 classic 在 VS Code/tmux/iTerm2「terminal scroll position jumps to the top while Claude is working, or the screen flashes」，且 classic 無法做程式內搜尋／點擊展開 |
| C. 現行＋OSC 52 | 同 A，另對支援的終端多送 OSC 52 | 約 15 行（base64＋`\x1b]52;c;…\x07`，經 `app.output.write_raw`） | Terminal.app 不支援、Warp 支援與否未驗證；只在 SSH 下有價值（Owner 現況是本機）。uah 與 Claude Code 都雙寫，但 gfargo 提醒有些終端把 OSC 52 內容回顯（GNU screen 曾把 base64 印到畫面），無確認機制 |

建議：**維持 A**，不切換。理由：A 與 Claude Code 自己的 fullscreen 路線、uah、dsh-TUI fullscreen 一致，Owner 六項需求已在 A 內以測試與 PTY 證明；B 不是「少 600 行」而是另一套 600 行以上的重寫，且帶進 inline 重繪與 scrollback 汙染這類更難測的 bug；C 對本機 Terminal.app 無效，暫不加。可選的小改進（不在本 PR，待 Owner 實機結果再決定）：(1) 滾輪倍率設定（Claude Code `CLAUDE_CODE_SCROLL_SPEED` 的做法，每事件列數可設 1–5，解決「移動太少」若真來自終端每格一事件）；(2) 邊緣自動捲動依指標超出距離加速（uah 每 tick 最多 8 行）；(3) 面板 F1/F2/F4 改用同一列模型。三者各約 20–40 行、低風險。

### 13.5 Q4 值得借鏡的 Claude Code TUI 行為（只借行為，不借程式碼）

已在本修法中具備：程式內選取、放開即複製、本機 `pbcopy`、Terminal.app 以 Fn 原生選取、有選取時 Ctrl+C 複製而非中斷、Esc 不清除選取、上捲暫停跟隨且新輸出不拉回、捲到底恢復跟隨、新輸出指示（本專案 `⇣新輸出`；Claude Code 為「Jump to bottom · N new messages」按鈕）、雙擊選詞、關閉滑鼠回報的開關（本專案 F6；Claude Code `CLAUDE_CODE_DISABLE_MOUSE`）。
值得後續借鏡（未實作）：滾輪倍率與快速滾動加速（`CLAUDE_CODE_SCROLL_SPEED`、`wheelScrollAccelerationEnabled`）；`Ctrl+Shift+C` 手動複製與「Copy on select」可關；Shift+方向鍵以鍵盤擴選、到邊緣時捲動；三擊選整行；PgUp/PgDn 捲半屏（本專案一屏）；上捲時頂列顯示「最近一則已捲出畫面的提問」；transcript 模式 `[` 把整段對話寫回終端 scrollback（讓 Cmd+F／原生選取可用，等於按需的 inline）；複製後 toast 告知用了哪條路徑；JetBrains/VS Code 終端的滾輪異常偵測。uah 另有：Esc 只清選取、`/copy` 整則回覆、依邊緣距離加速。

### 13.6 Q5 Terminal.app＋Warp：已驗證 vs 只能 Owner

| 事項 | 狀態 | 依據 |
|---|---|---|
| Terminal.app 原生選取繞過鍵 = Fn；Warp／多數終端 = Shift；iTerm2 = Option | 文件一致（Claude Code 官方、gfargo），本機未操作 | §8 步驟 7 由 Owner 確認 |
| Terminal.app 無 OSC 52；本機複製走 pbcopy | 文件一致（Claude Code 官方本機用 pbcopy；pageton/gfargo 未列 Terminal.app 支援 OSC 52） | pbcopy 參數與執行緒：自動測試 PASS；真 pbcopy→他 App 貼上：Owner |
| Terminal.app 回報 1002/1006 拖曳事件 | 未由本機證實；Claude Code fullscreen 在 Terminal.app 的拖選依賴同一機制 | Owner 步驟 4；若無反白即表示未回報 |
| Warp：滑鼠回報、Shift-drag、Cmd+C 無選取時是否送 ^C | 未驗證（Warp 文件僅說 Shift 可繞過、Cmd-R 切換滑鼠回報）；已以「有選取時 Ctrl-C 不離開」防護 | Owner 於 Warp 重做 §8 |
| 觸控板慣性／反向、滾輪事件密度 | 無法在 PTY 產生；Claude Code 文件指出不同終端每格事件數不同且程式無法偵測 | Owner；若「移動太少」再加倍率設定（§13.4 (1)） |
| 外層捲軸是否仍混淆 | alt screen 固有；Claude Code fullscreen 相同 | Owner 判斷是否可接受 |
| 本專案 PTY 已驗證（pyte／真實程序） | 滾輪每事件一列、閱讀位置、resize、拖選＋邊緣自動捲動、複製內容逐字相同、串流、Esc、Ctrl-C、字素叢集不拆 | §7、§11 |

結論：研究沒有顯示比現行更簡單、更安全的改法；Claude Code 自己的 fullscreen 與兩個開源實作走的是同一條路。不切換設計；三項可選小改進列於 §13.4，待 Owner 實機驗收結果後由 PM 決定。

## 14. Codex Review v3（d5ca8dc）兩項 P2 的處理

1. **雙擊選詞繞過上限**：`select_word` 的邊界掃描改為每側最多 `WORD_SCAN_LIMIT`（= 1 MiB 字）且改走 `copy_range(start, end)`（先 `utf8_size` 分段量、超限即拒絕、不切字串）；拒絕時字詞仍反白、狀態列 `選取超過複製上限 1024 KB`。測試 `test_double_click_word_goes_through_the_byte_limit_and_a_bounded_scan`：40 萬個中文字的無空白長詞只量一次即拒、copier 未呼叫；`WORD_SCAN_LIMIT=5` 時只取每側 5 字。
2. **連續複製並行、舊結果覆蓋**：改為單一工作執行緒＋「只保留最新請求」佇列（`_copy_next`）＋世代號（`_copy_generation`）：新請求取代仍在排隊的舊請求；正在執行的舊 pbcopy 跑完後若已被取代，結果與狀態一律忽略；因此 pbcopy 永不並行、剪貼簿最後一定是最新選取、狀態描述的是最新那次。測試 `test_rapid_copies_are_serialized_and_the_newest_wins`：慢 copier 阻塞第一次時連送兩次，實際呼叫序列 `['first', 'third']`、最大並行 1、第一次刻意失敗但狀態為 `已複製 5 字`（third）。
3. **全路徑自查**：所有複製入口（拖曳放開 `end_drag`、雙擊 `select_word`、Ctrl-C `copy_selection`）都經 `copy_range`，且 `copy()` 本身再量一次上限，不存在繞過路徑；無全選、無三擊。`tests.test_chat_tui` 154/154 PASS。

## 15. TUI QA（6a5524a 實測）D1/D2 與 O1–O3 的處理

- **D1 邊緣列單擊會捲動、選取並覆寫剪貼簿**：根因確認為 `chat_tui.py` `_pane_mouse` 在 MOUSE_UP 無條件呼叫 `_drag_to`，而 `_drag_to` 把面板第一列／最後一列視為邊緣。修法：記錄按下格 `_drag_origin`，只有指標離開該格後才算拖曳（`_drag_moved`）；MOUSE_MOVE 未移動則忽略；MOUSE_UP 不再捲動，只在曾移動時以 `_extend_to` 延伸到指標格；單擊、原地雙擊不捲動、不選取（雙擊只選詞）、不碰剪貼簿；邊緣自動捲動只在真正拖曳後才啟動（邊緣列仍算邊緣，因 Terminal.app 會把離開視窗的拖曳夾到邊緣列）。測試 `test_click_on_the_first_or_last_row_never_scrolls_selects_or_copies`（頂列、底列單擊、原地 MOVE、頂列雙擊選到該格的字、跟隨中點頂列仍跟隨）。PTY `pty_click.py`：頂/底列單擊 view moved=False、copied=False；頂列雙擊只複製該格的字 `text`、畫面不動。
- **D2 >4096 行時每個串流 chunk 全部重折**：根因為 `Wrapper` 快取滿 4096 時整個 `clear()`，且 `layout` 每次 `set_text` 全文重折。修法：`set_text` 以分段比較（`common_prefix_length`）記錄最早變動的 offset；`layout` 在寬度不變時只重折從該 offset 所在邏輯行起的尾段（`_rewrap_tail`），前面各行的列、起點與每行列數（`_line_rows`）原樣保留；寬度改變才全文重折；快取改為只淘汰最舊一筆。QA 的 `perf_view.py` 重跑：1000/3000/6000 行每 chunk 1.3/4.2/476 ms → **0.3/0.7/1.1 ms**；另測 10000/20000 行 2.4/4.0 ms。測試 `test_streamed_chunks_rewrap_only_the_changed_tail`：10000 行 100 個 chunk 每次 `line_rows` 呼叫 ≤ 2、每 chunk < 50 ms（寬鬆供 CI），尾段替換與改寬後結果與全文重折逐列相等。
- **O1 選取久留使第一個 Ctrl-C 只重複複製**：Ctrl-C 有選取時仍是複製並清除，但狀態列改為 `已複製 N 字；選取已清除，再按 Ctrl-C 中斷/離開`（3 s），使用者知道下一次 Ctrl-C 會中斷。未採「捲出畫面即清除」以免選取無故消失。
- **O2 窄寬度時 F6 滑鼠狀態被擠掉**：`STATUS_DROP_ORDER` 改為 `help, usage, model, provider, scroll, mouse`：先去 F1 提示、token、model、provider，滑鼠狀態最後才去。測試 `test_status_bar_drops_low_priority_fields_when_narrow` 更新（50/40/30/20 欄）。
- **O3 來回 resize 每次往上漂一列**：根因為每次重折後把錨點重設為新頂列的起點。修法：重折後保留原錨點 offset（`_clamp(keep_anchor=True)`），只有使用者捲動或夾限改變了頂列才更新錨點。測試 `test_resize_keeps_the_text_at_the_top_of_the_view` 加入 80→45→80 三次往返 `top` 不變。

`tests.test_chat_tui` 156/156 PASS。

## 16. Codex Review v4（bc417cb）三項的處理

1. **P2 複製工作執行緒競態**：「佇列空→工作執行緒結束」與「入列→確保有工作執行緒」改為同一把鎖下的原子操作：工作執行緒看到佇列空時在鎖內先把 `_copy_thread` 清為 None 再返回；`copy()` 在鎖內以 `_copy_thread is None` 判斷是否啟動新執行緒（不再看 `is_alive()`）；`copy_pending()`/`wait_copy()` 也以鎖下的參照為準。測試 `test_copy_worker_exit_and_enqueue_are_atomic`：模擬「舊執行緒物件仍 alive 但已走完最後一輪」的狀態，之後的 `copy('late')` 必定被處理；另以兩個執行緒各 200 次連發壓力測試，結束時佇列與參照皆空、狀態描述最後一筆。
2. **P2 Ctrl-C 在 F2/F3/F4 面板被隱藏的選取吃掉**：Ctrl-C 只在 `conversation_visible()` 且有選取時走複製路徑；任何面板切換（`toggle_help/detail/list/events`）都呼叫 `_leave_conversation()` 清除選取與拖曳狀態。
3. **P3 開 detail/list/events 未停止邊緣自動捲動**：同一個 `_leave_conversation()` 停止計時器、清除拖曳指標與選取。測試 `test_pane_switch_ends_drag_timer_and_selection_so_ctrl_c_interrupts_again`：拖到邊緣（計時器已排程）後按 F4，計時器為 None、`dragging` False、選取 None；再選取後開列表面板，選取被清除，Ctrl-C 直接離開程式；整個流程只有拖曳放開那一次複製。

`tests.test_chat_tui` 158/158 PASS。
