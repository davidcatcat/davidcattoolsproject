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
