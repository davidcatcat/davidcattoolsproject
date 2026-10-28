# main (/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/tui-test-poc-20261029/tools/llmtool)

### A start and render — PASS
TUI rendered in 670 ms (alternate screen, banner, rule, prompt and status row). child: 73167  42384  87.0 00:01
```text
LLMTool interactive chat (full-screen)                                         ^
provider: slow-fake
type: openai-compatible
skill: none
conversation turns: 0
History stays in memory. F1 or /help for help; Ctrl-C exits.
Chat mode talks to the chat provider directly; it is not a PM. /pm connect (or
/pm new) talks to the dedicated PM. /task submit is for development testing onl
y: it records a Task and dispatches nothing.
                                                                               v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | idle | t:UNKNOWN | 滾輪/Fn+↑↓ 捲動對話 | F1
```

### B conversation taller than the terminal — PASS
reply of 73 logical lines rendered; first line "第 1 行" scrolled off: true; END MARKER visible at bottom. child: 73167  43064  15.0 00:01
```text
row after paragraph 30                                                         ^
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一
段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長
的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文
段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，
用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測
試在終端機中自動換行之後的捲動與選取行為。 And here is some English text append
ed so that the wrapped rows mix CJK and ASCII widths.  And here is some English
 text appended so that the wrapped rows mix CJK and ASCII widths.  And here is
some English text appended so that the wrapped rows mix CJK and ASCII widths.
END MARKER 結尾                                                                v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1
```

### C wheel up/down (15 + 15 SGR wheel events) — FAIL
top row changed on 1/15 wheel-up events; returned to the same top row after 15 wheel-down: false.
top rows after each wheel-up: ["row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29","row after paragraph 29"]
status after up: > | p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1
```text
row after paragraph 29                                                         ^
row after paragraph 30
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一
段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長
的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文
段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，
用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測
試在終端機中自動換行之後的捲動與選取行為。 And here is some English text append
ed so that the wrapped rows mix CJK and ASCII widths.  And here is some English
 text appended so that the wrapped rows mix CJK and ASCII widths.  And here is
some English text appended so that the wrapped rows mix CJK and ASCII widths.  v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1
--- after 15 down ---
row after paragraph 29                                                         ^
row after paragraph 30
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一
段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長
的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文
段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，
用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測
試在終端機中自動換行之後的捲動與選取行為。 And here is some English text append
ed so that the wrapped rows mix CJK and ASCII widths.  And here is some English
 text appended so that the wrapped rows mix CJK and ASCII widths.  And here is
some English text appended so that the wrapped rows mix CJK and ASCII widths.  v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1
```

### D drag-select (press, move, release) — FAIL
status after release: "> | p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1"; cells with inverse style in rows 2-4: 0 (sample cell keys: x,y,char,fg,bg,bold,dim,italic,inverse,invisible,strike,blink,underline,underline_style,underline_color,link,link_id); pane text unchanged by the drag: true
```text
row after paragraph 29                                                         ^
row after paragraph 30
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一
段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長
的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文
段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，
用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測
試在終端機中自動換行之後的捲動與選取行為。 And here is some English text append
ed so that the wrapped rows mix CJK and ASCII widths.  And here is some English
 text appended so that the wrapped rows mix CJK and ASCII widths.  And here is
some English text appended so that the wrapped rows mix CJK and ASCII widths.  v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1
```

### E obtainable selection / clipboard content — NOT_SUPPORTED
tui-test session clipboard (OSC 52 only): "" -> the app copies with pbcopy, which the tool cannot observe.
harness file stand-in for pbcopy: not written; copied text matches the dragged rows on screen: 


### G resize 80x24 -> 60x30 -> 80x24 keeps the reading position — FAIL
top row before: "row after paragraph 29                                                         ^" | at 60x30: "row after paragraph 27                                     ^" | back at 80x24: "row after paragraph 29                                                         ^"
```text
row after paragraph 27                                     ^
row after paragraph 28
row after paragraph 29
row after paragraph 30
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲
動與選取行為。這是一段很長的中文段落，用來測試在終端機中自
動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測
試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中
文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這
是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動
與選取行為。這是一段很長的中文段落，用來測試在終端機中自動
換行之後的捲動與選取行為。 And here is some English text ap
pended so that the wrapped rows mix CJK and ASCII widths.
And here is some English text appended so that the wrapped
rows mix CJK and ASCII widths.  And here is some English te
xt appended so that the wrapped rows mix CJK and ASCII widt
hs.                                                        v
------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s
--- back to 80x24 ---
row after paragraph 29                                                         ^
row after paragraph 30
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一
段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長
的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文
段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，
用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測
試在終端機中自動換行之後的捲動與選取行為。 And here is some English text append
ed so that the wrapped rows mix CJK and ASCII widths.  And here is some English
 text appended so that the wrapped rows mix CJK and ASCII widths.  And here is
some English text appended so that the wrapped rows mix CJK and ASCII widths.  v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1
```

### H1 F6 mouse reporting off / on (status field) — PASS
(at 120 cols so the status row keeps its mouse field; before F6: "> | p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F6 滑鼠:開 | F1")
F6 -> status shows 滑鼠:關: true; F6 again -> 滑鼠:開: true; wheel scrolls again afterwards: false
```text
row after paragraph 27                                                                                                 ^
row after paragraph 28
row after paragraph 29
row after paragraph 30
row after paragraph 31
row after paragraph 32
row after paragraph 33
row after paragraph 34
row after paragraph 35
row after paragraph 36
row after paragraph 37
row after paragraph 38
row after paragraph 39
row after paragraph 40
這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測試在終端機中自動
換行之後的捲動與選取行為。這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段
落，用來測試在終端機中自動換行之後的捲動與選取行為。這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取
行為。這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。 And here is some English text appended s
o that the wrapped rows mix CJK and ASCII widths.  And here is some English text appended so that the wrapped rows mix
CJK and ASCII widths.  And here is some English text appended so that the wrapped rows mix CJK and ASCII widths.
END MARKER 結尾                                                                                                        v
------------------------------------------------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | Fn+↑↓ 捲動對話 | F6 滑鼠:關 | F1
```

### H1b wheel while mouse reporting is off reaches the terminal, not the app — NOT_SUPPORTED
3 wheel-up events with reporting off left the top row unchanged: false. tui-test encodes mouse input as SGR bytes regardless of the DEC private modes the app has set (probe: an app with only ?1049 set still receives \x1b[<64;..M and motion \x1b[<35;..M), so a real terminal's "mouse off => terminal selects/scrolls natively" path cannot be exercised.

### H2 input history not regressed (Up recalls 'hello') — FAIL
rows below the rule after Up: ["--------------------------------------------------------------------------------",">","p:slow-fake | m:fake-stream | ready 0.0s | t:1+2=3 | 滾輪/Fn+↑↓ 捲動對話 | F1"]

long session child stats at exit: 73167  46076   3.1 00:09

### F streaming while scrolled up: position stable, then follow again — PASS
top row after 5 wheel-up during the stream: "LLMTool interactive chat (full-screen)                                         ^"; same after 1 s of further chunks: true (screen kept changing meanwhile: true); status then: > | p:slow-fake | m:fake-stream | working 1.1s | t:UNKNOWN | 滾輪/Fn+↑↓ 捲動對話
after wheel-down to the bottom: status "> | p:slow-fake | m:fake-stream | working 5.8s | t:UNKNOWN | 滾輪/Fn+↑↓ 捲動對話", screen follows new chunks again: true
```text
LLMTool interactive chat (full-screen)                                         ^
provider: slow-fake
type: openai-compatible
skill: none
conversation turns: 0
History stays in memory. F1 or /help for help; Ctrl-C exits.
Chat mode talks to the chat provider directly; it is not a PM. /pm connect (or
/pm new) talks to the dedicated PM. /task submit is for development testing onl
y: it records a Task and dispatches nothing.
you:
go
assistant:
Streaming reply 串流回覆 字詞001 字詞002 字詞003 字詞004 字詞005 字詞006 字詞00
7 字詞008 字詞009 字詞010 字詞011 字詞012 字詞013 字詞014 字詞015 字詞016 字詞0
17 字詞018 字詞019 字詞020 字詞021 字詞022 字詞023 字詞024 字詞025 字詞026 字詞
027 字詞028 字詞029 字詞030 字詞031 字詞032 字詞033 字詞034 字詞035 字詞036 字
詞037 字詞038 字詞039 字詞040 字詞041 字詞042 字詞043 字詞044
                                                                               v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | working 2.1s | t:UNKNOWN | 滾輪/Fn+↑↓ 捲動對話
--- after wheel-down ---
                                                                               ^
you:
go
assistant:
Streaming reply 串流回覆 字詞001 字詞002 字詞003 字詞004 字詞005 字詞006 字詞00
7 字詞008 字詞009 字詞010 字詞011 字詞012 字詞013 字詞014 字詞015 字詞016 字詞0
17 字詞018 字詞019 字詞020 字詞021 字詞022 字詞023 字詞024 字詞025 字詞026 字詞
027 字詞028 字詞029 字詞030 字詞031 字詞032 字詞033 字詞034 字詞035 字詞036 字
詞037 字詞038 字詞039 字詞040 字詞041 字詞042 字詞043 字詞044 字詞045 字詞046
字詞047 字詞048 字詞049 字詞050 字詞051 字詞052 字詞053 字詞054 字詞055 字詞056
 字詞057 字詞058 字詞059 字詞060 字詞061 字詞062 字詞063 字詞064 字詞065 字詞06
6 字詞067 字詞068 字詞069 字詞070 字詞071 字詞072 字詞073 字詞074 字詞075 字詞0
76 字詞077 字詞078 字詞079 字詞080 字詞081 字詞082 字詞083 字詞084 字詞085 字詞
086 字詞087 字詞088 字詞089 字詞090 字詞091 字詞092 字詞093 字詞094 字詞095 字
詞096 字詞097 字詞098 字詞099 字詞100 字詞101 字詞102 字詞103 字詞104 字詞105
字詞106 字詞107 字詞108 字詞109 字詞110 字詞111 字詞112 字詞113 字詞114 字詞115
 字詞116 字詞117 字詞118 字詞119 字詞120 字詞121 字詞122 字詞123 字詞124 字詞12
5 字詞126 字詞127 字詞128 字詞129 字詞130 字詞131 字詞132 字詞133 字詞134 字詞1
35 字詞136 字詞137
                                                                               v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | working 6.2s | t:UNKNOWN | 滾輪/Fn+↑↓ 捲動對話
```

### H3 lone Esc interrupts the stream — PASS
"interrupt" shown after a lone Esc: true (972 ms after the key; prompt_toolkit flushes a lone Esc after its timeoutlen); screen stable 0.8 s later (no more chunks): true
```text
                                                                               ^
assistant:
Streaming reply 串流回覆 字詞001 字詞002 字詞003 字詞004 字詞005 字詞006 字詞00
7 字詞008 字詞009 字詞010 字詞011 字詞012 字詞013 字詞014 字詞015 字詞016 字詞0
17 字詞018 字詞019 字詞020 字詞021 字詞022 字詞023 字詞024 字詞025 字詞026 字詞
027 字詞028 字詞029 字詞030 字詞031 字詞032 字詞033 字詞034 字詞035 字詞036 字
詞037 字詞038 字詞039 字詞040 字詞041 字詞042 字詞043 字詞044 字詞045 字詞046
字詞047 字詞048 字詞049 字詞050 字詞051 字詞052 字詞053 字詞054 字詞055 字詞056
 字詞057 字詞058 字詞059 字詞060 字詞061 字詞062 字詞063 字詞064 字詞065 字詞06
6 字詞067 字詞068 字詞069 字詞070 字詞071 字詞072 字詞073 字詞074 字詞075 字詞0
76 字詞077 字詞078 字詞079 字詞080 字詞081 字詞082 字詞083 字詞084 字詞085 字詞
086 字詞087 字詞088 字詞089 字詞090 字詞091 字詞092 字詞093 字詞094 字詞095 字
詞096 字詞097 字詞098 字詞099 字詞100 字詞101 字詞102 字詞103 字詞104 字詞105
字詞106 字詞107 字詞108 字詞109 字詞110 字詞111 字詞112 字詞113 字詞114 字詞115
 字詞116 字詞117 字詞118 字詞119 字詞120 字詞121 字詞122 字詞123 字詞124 字詞12
5 字詞126 字詞127 字詞128 字詞129 字詞130 字詞131 字詞132 字詞133 字詞134 字詞1
35 字詞136 字詞137 字詞138 字詞139 字詞140 字詞141 字詞142 字詞143 字詞144 字詞
145 字詞146 字詞147 字詞148 字詞149 字詞150 字詞151 字詞152 字詞153 字詞154 字
詞155 字詞156 字詞157 字詞158
llmtool: reply interrupted after the text above; it stays in history as shown  v
--------------------------------------------------------------------------------
>
p:slow-fake | m:fake-stream | interrupted | t:UNKNOWN | 滾輪/Fn+↑↓ 捲動對話 | F1
```

### H4 PM streaming — NOT_VERIFIED
Not run: needs a fake `claude` CLI on PATH and /pm new + /pm connect in a scratch state root; out of this PoC's budget (the pyte recipe already covers it).
