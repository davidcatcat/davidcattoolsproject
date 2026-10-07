# llmtool A11b.2.2 B5 containment & failure semantics spec — 獨立 design／security review（2026-10-07）

## 範圍與方法

- **審查對象：**
  - PR #44，branch `docs/llmtool-a11b2-2-b5-containment-spec-20261007`，**exact head `1ed7551da25633b8839005a202a565cbacc55a43`**；
  - base `main @ 780970ab`，merge base 相同。
- **變更：**
  - B5 spec：810 行；
  - `tools/llmtool/README.md`：+7／−4；
  - `ROADMAP.md`：+6／−1，狀態同步。
- **方式：**
  1. 完整閱讀 spec，並對照 A11b.2.1 的 `git_mutation.py` 和 QG-S1 R-EXEC-6／R-AUD-4。
  2. **主動在實際的 macOS 上執行 process 和 signal 的 probe**（Apple Python 3.9.6，scratchpad，所有 process 都只用 `sleep` 或 `true`），驗證 spec 依賴的 OS 語意：
     - **K-A：** leader 是 zombie（還沒有被 reap），但同一個 group 中有活著的 descendant → `killpg` **成功**，descendant 被殺死；
     - **K-B：** leader **已經被 reap**，但 descendant 還活著 → group 仍然存在，`killpg` **成功**；
     - **K-C：** **macOS Python 沒有 `os.waitid`**（`AttributeError`），所以無法用 `WNOWAIT` 在「不 reap 的情況下偵測 leader 已經結束」；
     - **K-D**（上一輪已經驗證）：group 中**只剩下 zombie** 時，`killpg` 會產生 `PermissionError`；
     - **L-1：** 真實的 liveness pipe 模型：parent 用 `pass_fds` 只傳 read end 給 exec 出來的 supervisor（`-I`，`start_new_session`），fake Git 是一個獨立的 group。對 parent 送出 `SIGKILL` 之後，四種情境的結果：

       | parent 在 mutation 期間做了什麼 | supervisor 是否看到 EOF | 結果 |
       |---|---|---|
       | 什麼都沒做 | **EOF** | 殺死 fake Git group |
       | 另外 `Popen(close_fds=True)` 一個無關的子 process | **EOF** | 殺死 fake Git group |
       | **bare `os.fork()` 一個子 process**（例如 multiprocessing 的 fork、C extension） | **完全沒有 EOF** | fake Git 一直存活，直到我的 probe 自己的 8 秒保護機制殺死它 |
       | **writer 被設為 inheritable，然後用 `close_fds=False` spawn 子 process** | **完全沒有 EOF** | 同上 |

- **沒有做的事：**
  - 沒有實作任何 production 或 test code；
  - 沒有執行 `git add`；
  - 沒有開始 A11b.2.2.x；
  - 沒有修改 spec；
  - 沒有 merge。

## 結論：BLOCKED（只阻擋 A11b.2.2.1；A11b.2.2.0 可以開始）

**架構的方向是正確的。** 我在 macOS 上實際驗證了核心機制：dedicated supervisor process、parent-liveness pipe 的 EOF、獨立的 Git process group、雙向的 fallback、不自動刪除 lock、post-state 分類、durable audit 作為 runtime gate。

但是，「**設計看起來安全，但在實際的 process 語意下會失效**」的反例確實存在：

1. **H1：** parent 唯一的 writer 會因為 fork 或繼承而被保留，導致 parent SIGKILL 之後**永遠收不到 EOF**（已經實際重現）；
2. **H2：** PGID 和 PID 的重複使用，可能讓 cleanup **殺錯 process group**；而 macOS 沒有 `waitid`，spec 隱含的「先偵測結束、再 killpg、最後 reap」的順序無法用一般的 `wait` 達成。

另外有 2 個 MEDIUM 和 harness 有關（M1、M2），1 個 MEDIUM 和 2.2.2 的分類有關（M3）。這些都可以用一份 **spec addendum（只修改文件）** 解決。

## Findings

### H1 — HIGH — parent-liveness writer 被保留，SIGKILL 之後收不到 EOF（阻擋 2.2.1）

- **問題：** spec §3.2 第 3 項和第 5 項要求「parent 持有唯一的 writer」、「Git child 不能繼承 writer」，但是：
  - (a) 沒有規定 **supervisor 必須用 exec 的方式 spawn**；
  - (b) 沒有限制 parent 在 mutation 期間**是否可以 fork 或 spawn 其他子 process**。
- **重現（L-1）：** parent 在 mutation 期間 bare `os.fork()` 一個子 process，或者讓 writer 可以被繼承之後用 `close_fds=False` spawn 子 process。這時 parent 被 SIGKILL，**supervisor 永遠看不到 EOF**，只要那個子 process 還活著，fake Git 就會繼續執行。
  - 如果 supervisor 本身是用 `multiprocessing` 的 fork 方式啟動的，它會**繼承自己的 writer 副本**，結果也是永遠收不到 EOF。
- **為什麼現有的 spec 無法保證安全：**
  - Python 的 PEP 446（fd 預設不可繼承）和 `close_fds=True` 只能防止 **exec 時**的繼承，**無法防止 `fork()` 不接 exec 的情況**；
  - spec §6 只約束了 Git child。
- **需要修改的規範：**
  1. supervisor **只能**用 fork+exec 的方式 spawn（`subprocess.Popen` 或 `posix_spawn`），而且 `pass_fds` 只能包含 liveness pipe 的 **read end** 和 status pipe 的 **write end**。禁止 `multiprocessing` 的 fork 或 forkserver，也禁止 bare `os.fork`；
  2. liveness pipe 必須用 `O_CLOEXEC` 建立（Python 預設就是不可繼承），**而且整個 mutation window 期間，parent 不能 fork 或 spawn 任何其他子 process**；mutation 呼叫期間只允許 main thread（spec §9 已經要求）。實作上，可以在進入 mutation 時檢查 `threading.active_count() == 1`，並拒絕 `multiprocessing` 的 fork start method；
  3. supervisor 的 status pipe 的 writer 同樣**不能**被 Git child 繼承（spec §6 寫的是「control/audit/liveness FD」，應該明確加入 **status pipe**），否則 supervisor 死掉時，parent 也會因為 Git 持有 writer 而看不到 status EOF；
  4. parent 偵測 supervisor 是否還活著，**不能只依賴 status EOF**。parent 是 supervisor 的 parent，必須同時使用 `waitpid`／`poll()` 或 kqueue `NOTE_EXIT`。
- **需要加入的 test（2.2.1）：**
  - L-1 中 `Popen(close_fds=True)` 的變體 → 必須 EOF；
  - 「mutation 期間有其他 thread」→ 必須拒絕進入；
  - Git child 的 fd 表中**不包含** liveness 或 status 的 fd（例如讓 fake child 列出自己的 `/dev/fd`）。
- **是否阻擋：** 2.2.0 → 否；**2.2.1 → 是**。

### H2 — HIGH — PGID／PID 重複使用，以及在 macOS 上「先 killpg 再 reap」的順序無法用 wait 達成（阻擋 2.2.1）

- **問題 A（supervisor 這一側）：**
  - spec §8.4 要求「leader 結束之後，仍然要對 process group 執行 cleanup」。
  - 但如果 leader 已經被 `wait()` reap，而且**沒有 descendant**，group 就不存在了，這個 ID 可能被重複使用（K-B 證明：只要還有 descendant，group 就會存在，這時 killpg 是安全的）。對已經消失的 group 執行 killpg，**有可能殺死一個同 UID、剛好重複使用這個 ID 的無關 process group**。
  - 正確的順序是：**在 leader 還是 zombie、尚未 reap 的時候執行 killpg**（zombie 會佔住這個 ID），然後才 reap。但 macOS Python **沒有 `os.waitid`**（K-C），無法用 `WNOWAIT` 偵測結束。而一般的 `Popen.poll()` 和 `wait()` 會在偵測的同時就 reap。
- **問題 B（parent fallback）：**
  - supervisor 被 SIGKILL 之後，Git 會被 reparent 給 launchd。Git 結束之後，**launchd 會 reap 它**，PGID 就被釋放了。
  - parent 之後如果用「已知的 PGID」執行 killpg，ID 有可能已經被重複使用。parent 無法佔住這個 ID，因為它不是 Git 的 parent。
- **為什麼現有的 spec 無法保證安全：** spec §8.3 第 4 項寫的是「always wait/reap」，沒有規定 killpg 和 reap 的先後順序。§11 只寫了「kill the known Git process group」，沒有任何身分綁定。
- **需要修改的規範：**
  1. supervisor **在 reap Git leader 之前**必須完成 killpg。偵測 leader 結束時，必須使用**不會 reap 的機制**：在 macOS 上用 `select.kqueue` 的 `KQ_FILTER_PROC`／`NOTE_EXIT`，或者用 stdout／stderr 的 EOF 作為訊號。在 killpg 之前，禁止呼叫 `poll()` 或 `wait()`。
  2. parent fallback 在 killpg 之前，必須驗證身分：
     - spawn 時記錄 Git leader 的 **start time**（例如 `sysctl kern.proc.pid` 的 `p_starttime`）；
     - 執行 fallback 時，如果 `pid == pgid` 的 process 存在，但 start time 不同 → 判定為重複使用，**不執行 kill**，分類為 `MUTATION_STATE_UNKNOWN` 或 `USER_ACTION_REQUIRED`；
     - 如果 leader 不存在，但 group 中還有成員 → 依照 POSIX，ID 不會被重複使用，可以 killpg。
  3. test：在 fake leader 已經結束並被 reap 之後，確認不會對已經消失的 group 送出 signal；在 parent fallback 中，模擬 start time 不一致時不會 kill。
- **是否阻擋：** 2.2.0 → 否；**2.2.1 → 是**。

### M1 — MEDIUM — supervisor 卡住（沒有死掉）時，parent 的 bounded wait 沒有升級處理（阻擋 2.2.1）

- **問題：**
  - spec §9 寫著「waits a bounded interval for supervisor cleanup … restores … raises」；
  - §11 只涵蓋了 supervisor **結束**、channel **關閉**和 status **格式錯誤**這三種情況。
- **反例：** supervisor 因為 bug 陷入無窮迴圈，或者卡在某個 syscall 上，沒有讀取 control channel。parent 收到 SIGINT，送出 CANCEL，等待逾時之後**直接回傳 interrupted**。parent 仍然活著，所以**不會產生 EOF**；即使 parent 關閉了 writer，卡住的 supervisor 也不會處理。**supervisor 和 Git 都還在執行。**
- **需要修改的規範：** bounded wait 逾時，或者在任何「沒有收到 supervisor 確認 Git group 已經消失」的退出路徑上，parent 都必須：
  1. 對 supervisor 送出 `SIGKILL` 並 reap（它是 parent 的子 process，不會有重複使用的問題）；
  2. 依照 H2 的身分驗證，對 Git group 送出 SIGKILL；
  3. 分類為 `MUTATION_SUPERVISOR_LOST` 或 `MUTATION_STATE_UNKNOWN`。

  **任何**退出路徑都不能讓 supervisor 或 Git 繼續存活。
- **test：** 讓 fake supervisor 忽略 CANCEL 並停住，然後確認 parent 逾時之後，supervisor 和 fake Git group 都被清除。
- **是否阻擋：** **2.2.1 → 是**。

### M2 — MEDIUM — 沒有規定 supervisor 的 interpreter、環境變數和 code 版本的綁定（阻擋 2.2.1）

- **問題：** supervisor 是一個新 exec 出來的 Python process。spec 沒有規定以下三件事：
  - (a) 使用哪一個 interpreter，以及啟動的方式；
  - (b) supervisor 本身的環境變數；
  - (c) supervisor 載入的 code，必須和 parent 已經 review 過的 code 是同一個版本。
- **反例：**
  - ambient 的 `PYTHONPATH`、`PYTHONSTARTUP`、`PYTHONHOME`、`DYLD_*` 或使用者的 site-packages，可以讓 supervisor 載入不同的 `llmtool` 或額外的 code；
  - 如果 parent 啟動之後，使用者在磁碟上更新了 `tools/llmtool`，supervisor 會用**新的 code** 重新執行 preflight 並 spawn Git。這不是 parent 看過的那個版本。
- **需要修改的規範：**
  1. 啟動方式是 `[sys.executable, '-I', '-m', '<private supervisor module>']`，`-I` 會忽略 `PYTHON*` 環境變數和 user site；
  2. supervisor 使用**全新的 allowlist 環境變數**，只有 `PATH` 和 `LC_ALL`，不能有 `PYTHON*`、`DYLD_*`、`LD_*`，也不能有任何 credential；
  3. handshake 中包含 parent 計算的 **llmtool code digest**（`git_mutation.py`、supervisor module、`action_contract.py`、`errors.py` 等 artifact 的 SHA-256）。supervisor 啟動後重新計算，**不一致就拒絕**；
  4. supervisor module 如果沒有收到正確的 handshake（例如 stdin 是 TTY、沒有 `pass_fds`、或者 version 和 digest 不正確），就立即以 fail closed 的方式結束。這樣它就不會變成隱藏的 CLI 入口。
- **是否阻擋：** **2.2.1 → 是**。

### M3 — MEDIUM — post-state 的分類不是全序，缺少 no-op 和 HEAD 的處理（阻擋 2.2.2；2.2.1 不受影響）

- **問題：**
  1. **no-op action：** A11b.2.1 的 preflight **不要求** target 的內容必須和 index 不同。如果被授權的檔案內容就是 index 中的 blob，那麼「預期的 post-state」等於「pre-state」，§14.2 的 NOT_APPLIED 和 §14.1／§14.3 的 APPLIED **同時成立**，分類無法決定。
  2. §14.2 寫著「**raw**/semantic index remains exactly the pre-state」。如果 raw bytes 改變了（例如 stat 被更新），但語意上仍然等於 pre-state，這種情況**沒有被分類**，而且和 B4「raw 只能作為證據」的原則矛盾。
  3. §13 的 post-verify **沒有重新驗證 HEAD 和 branch ref**。如果在 mutation 期間有其他 process 做了 commit，語意 index 的比較就失去意義。
- **需要修改的規範：** 定義一個**有優先順序的分類表**，只依照語意狀態判斷：
  1. lock 存在，或者 index 無法讀取 → `USER_ACTION_REQUIRED`；
  2. HEAD 或 branch ref 和 pre 不同 → `USER_ACTION_REQUIRED`；
  3. 語意上等於 expected（包括 no-op 的情況）→ 如果 exit 0 而且沒有中斷，就是 `APPLIED`；否則是 `APPLIED_BUT_CONTROL_INTERRUPTED`；
  4. 語意上等於 pre（而且不等於 expected）→ `NOT_APPLIED`；
  5. 其他 → `USER_ACTION_REQUIRED`。

  另一個做法是在 preflight 階段就把 no-op action 視為一種狀態，或者直接 reject。
- **是否阻擋：** 2.2.1 → 否（harness 不涉及 Git 的語意）；**2.2.2 → 是**。

### L1 — LOW — control／status protocol 的 framing 和 EPIPE（2.2.1 實作時處理）

- **需要規範：**
  - 每一則訊息都使用長度前綴（例如 4 bytes），並且有最大長度；
  - 讀取時必須迴圈處理 partial read，**訊息讀到一半就遇到 EOF 時，一律視為 cancel**；
  - 不認得的 version 或 type → cancel；
  - 送出 request 之後，parent 只能再送出 CANCEL；
  - status 也使用相同的 framing。
- **EPIPE：** Python 預設忽略 SIGPIPE。parent 對已經死掉的 supervisor 寫入 CANCEL，或者 supervisor 對已經死掉的 parent 寫入 status，都會產生 `BrokenPipeError`。這必須被當成對方已經消失，**不能讓 cleanup 中斷**。
- 讀寫都必須是 non-blocking 或者用 select 搭配 deadline，避免被惡意或異常的訊息卡住。

### L2 — LOW — 從 spawn Git 到 PGID 送達 parent 之間的空窗

- 從 Git 被 spawn 到 PGID 被送到 parent 的這段時間內，如果 supervisor 死掉，parent 就沒有 PGID 可以使用。fallback 在這段時間內無法生效，Git add 會跑完。
- 這會被 recovery 的分類（§17）涵蓋。建議在 spec 中明確寫出這個殘餘風險，並且讓 supervisor 在 spawn 之後**立即**送出 PGID，然後才開始其他的監看。
- 如果將來需要完全消除這個空窗，可以考慮讓 Git 以 suspended 的狀態啟動，並在收到 ack 之後才讓它繼續執行。但 Python 的 `posix_spawn` 沒有 `POSIX_SPAWN_START_SUSPENDED`，所以這次不強制要求。

### L3 — LOW — signal handler 安裝和恢復的順序

- **安裝：** 必須在建立 pipe 和 spawn supervisor **之前**安裝 handler。
- **恢復：** 必須在 supervisor **已經被 reap**、Git group 已經確認消失之後才恢復。
- **說明：** 在安裝之前或恢復之後收到 signal，會走預設的行為（parent 結束），這時由 EOF 機制兜底。這是這個設計好的地方：**安全性來自 EOF，handler 只是為了提供 typed outcome**。建議把這一點寫進 spec，讓實作者理解 handler 不是安全性的根據。

### NOTE

- **N1（macOS SIGKILL probe 的要求）：** spec §19 的 7 個步驟是正確的。補充以下要求：
  - 同時執行 H1 中 `Popen(close_fds=True)` 的變體；
  - **重複執行至少 20 次**，並且斷言從 parent 被 kill 到 Git group 消失的延遲有上限（例如 2 秒）；
  - 確認 process 是否存在時，使用 `kill(pid, 0)` 或 `killpg(pgid, 0)`，**不要使用 `ps`**（P1 的 validation 已經紀錄過，`ps` 在 Codex sandbox 中會被拒絕）；
  - probe 必須在真正的 Mac host 上、在 sandbox 以外執行，並記錄 OS 和 Python 的版本。
- **N2（`index.lock` 政策）：** 永遠不刪除 lock，這個政策是正確的。如果使用者本身的 Git 在同一時間持有 lock，會讓我們的 add 失敗，然後因為 lock 還存在而被分類為 `USER_ACTION_REQUIRED`。這是保守而安全的誤判，可以接受；輸出的訊息應該說明 lock 會阻擋後續的 Git 操作。
- **N3（Windows 和 Linux）：** 「POSIX／macOS 優先、原生 Windows NOT SUPPORTED、Linux 必須有等效的 test 才能支援」的邊界很清楚。Linux 可以額外使用 `PR_SET_PDEATHSIG` 作為第二層保護，但不能取代 EOF 機制。
- **N4（durable audit 和 private engine 的例外）：** 執行順序（intent 寫入成功 → 呼叫 engine → terminal 紀錄）和 fail closed 都是正確的。private engine 的例外，搭配 AST、字串和動態 import 的掃描（§21，也處理了 A11b.2.1 的 N4）是足夠的。字串掃描建議額外涵蓋：在 production code 中，以 `-m`、`subprocess` 或 `runpy` 的方式呼叫 supervisor module 的字串。
- **N5（同一個 UID 和同時死亡的邊界）：** spec §11 的描述是正確的：parent 和 supervisor 同時被強制終止，不在 process 層級可以保證的範圍內；惡意的同 UID process 不在隔離的範圍內。我的反例 H1 **不屬於**這個邊界，因為 fork 和繼承是 llmtool 自己的行為，所以必須修正，不能以這個邊界作為豁免。

## 各項目的判定依據

- **Dedicated supervisor 的架構：** 方向正確，L-1 的 plain 和 `Popen` 變體實際有效。但 spawn 的方式、interpreter 和 code 綁定必須寫明（H1、M2）。
- **parent SIGKILL 時的 control pipe EOF：** 只有在 H1 的條件下才成立。
- **supervisor 消失時的 parent fallback：** 缺少身分綁定（H2-B），也沒有處理 supervisor 卡住的情況（M1）。
- **leader 結束後的 descendant 清除：** K-A 和 K-B 證明 killpg 本身有效，但順序必須是「killpg → reap」，而偵測的方式要改用 kqueue（H2-A）。
- **L1 PermissionError：** §8.3 第 2–6 項加上 §18 的 2.2.0，完整涵蓋了 A11b.2.1 的 L1（K-D 已經驗證）。
- **SIGINT／SIGTERM／SIGHUP：** handler 只設定 flag 並通知 supervisor；只允許 main thread；每一個路徑都要恢復；使用真實的 signal 測試。這些都正確，L3 和 M1 是補充。
- **macOS SIGKILL test：** 必要而且正確，N1 是補充。
- **Pre-spawn revalidation：** 在 supervisor 中重新執行 preflight，並逐欄比對 evidence，這是正確的。前提是 M2 的 code 綁定，以及 L2 的空窗紀錄。殘餘的 TOCTOU 由 post-verify 處理，並且屬於同一個 UID 的邊界。
- **Post-state classification：** M3。
- **`index.lock` 政策、durable audit、public／import 邊界、同一個 UID 的邊界：** 都是 PASS（N2、N4、N5）。

## 判定

```text
A11b.2.2 B5 DESIGN REVIEW:
BLOCKED

Reviewed spec head:
1ed7551da25633b8839005a202a565cbacc55a43

Dedicated supervisor architecture:
finding (H1, M2) — sound and probe-validated on macOS, but must mandate fork+exec spawn with pass_fds limited to
  liveness-read/status-write, `-I` interpreter, fresh env allowlist, code-digest handshake, handshake-gated entry

Parent control-pipe EOF on SIGKILL:
finding (H1, HIGH) — reproduced: bare fork child or inheritable writer + close_fds=False child keeps the writer open,
  supervisor never sees EOF after parent SIGKILL; require O_CLOEXEC, no fork/other spawn during the window, main-thread
  only, status-writer exclusion from Git, and a Popen-variant probe

Supervisor-loss parent fallback:
finding (H2-B, M1) — fallback killpg by remembered PGID can hit a reused group after launchd reaps the orphaned Git;
  require leader start-time binding; hung (not dead) supervisor must be SIGKILLed + Git group verified-killed on
  bounded-wait expiry; detect supervisor exit via waitpid/kqueue, not only status EOF

Leader-exit descendant cleanup:
finding (H2-A) — killpg works with zombie leader or with reaped leader + live descendant (probes K-A/K-B), but cleanup
  must killpg BEFORE reaping; macOS Python lacks os.waitid/WNOWAIT (K-C) → use kqueue NOTE_EXIT / pipe EOF, never
  poll()/wait() before killpg

L1 PermissionError cleanup:
PASS

SIGINT/SIGTERM/SIGHUP semantics:
PASS (L3 ordering note; safety rests on EOF, handlers only provide typed outcome)

Actual macOS SIGKILL test requirement:
PASS (N1: add Popen variant, ≥20 repetitions with latency bound, kill(0)-based liveness, run outside sandbox)

Pre-spawn revalidation:
PASS (conditional on M2 code binding; L2 PGID-report window documented)

Post-state classification:
finding (M3, blocks 2.2.2 only) — define a total, prioritized, semantic-only table; handle no-op actions; re-verify
  HEAD/branch post-mutation

index.lock policy:
PASS

Durable-before-action audit interaction:
PASS

Public/import boundary:
PASS (N4: also scan for `-m`/subprocess/runpy invocation strings of the supervisor module)

Same-UID / simultaneous-death threat boundary:
PASS (H1 is llmtool-caused and not excused by this boundary)

New blockers:
H1 liveness-writer retention via fork/inheritance (blocks 2.2.1)
H2 PGID/PID reuse: killpg-before-reap via kqueue, start-time-bound parent fallback (blocks 2.2.1)
M1 hung-supervisor escalation on bounded-wait expiry (blocks 2.2.1)
M2 supervisor interpreter/env/code-digest/handshake binding (blocks 2.2.1)
M3 total semantic classification incl. no-op + HEAD re-verify (blocks 2.2.2)

May implement A11b.2.2.0:
YES

May implement A11b.2.2.1 containment harness:
NO — after a documentation-only spec addendum resolving H1, H2, M1, M2 (and recording L1–L3, N1) passes targeted review

May implement real git add (A11b.2.2.2):
NO
```

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #44 的 branch。沒有修改 production、test 或 spec，沒有 merge，也沒有開始 A11b.2.2.x。
- probe 的 script 和 log 只放在 session 的 scratchpad 中，沒有刪除，也沒有加入 Git。probe 中由我自己啟動的 `sleep` process，已經用 pattern 只結束了這些 probe 的 process。
