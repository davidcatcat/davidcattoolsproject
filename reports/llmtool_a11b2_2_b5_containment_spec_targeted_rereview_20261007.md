# llmtool A11b.2.2 B5 containment spec — targeted design／security re-review（2026-10-07）

## 範圍與方法

- **審查對象：**
  - PR #44，**remediation head `2335e842ba88d41bae51010f998ff7abdec338f2`**；
  - 上一輪審查的 spec head 是 `1ed7551`；
  - base `main @ 780970ab`。
- **remediation 的內容：** 只有一個 commit（`2335e84`），修改了：
  - B5 spec：+586／−94，新增 §3.3、§3.4、§8.1，修改 §9、§11.1–11.3、§14.1、§19、§21、§26；
  - 新增 remediation summary；
  - README 和 ROADMAP 的狀態同步。
- **確認：**
  - **我上一輪的 review 檔案和原本的 bytes 完全相同**（`git diff 23ae5e8 2335e84` 是空的）；
  - README 和 ROADMAP 只同步狀態，並明確寫出 2.2.x NOT AUTHORIZED，**沒有擴大授權**；
  - PR 中沒有 production、test 或 runtime 的修改。
- **方式：**
  - 完整閱讀 spec、我上一輪的 review 和 remediation summary；
  - **只**針對 H1、H2、M1、M2、M3，以及 L1、L2、L3、N1、N4 進行判斷；
  - 不重新做完整的 threat model。
- **一個補充的 macOS probe**（只用於確認 H2 的機制可行）：用 `select.kqueue` 對 Git leader 註冊 `KQ_FILTER_PROC`／`NOTE_EXIT`：
  - 對**活著的** leader：收到 1 個 event，`NOTE_EXIT=True`，結束之後 `returncode` 仍然是 None（**沒有被 reap**）；
  - 對**已經是 zombie** 的 leader：註冊之後**立即**收到 `NOTE_EXIT`，仍然沒有被 reap。

  所以 spec 要求的「不 reap 的結束偵測 → 決定是否 killpg → reap」在 macOS 上**可以實現**，而且 fast-exit 的 race（註冊之前就已經結束）也會立即回報，不會錯過。
- **沒有做的事：** 沒有修改 spec、README、ROADMAP、production 或 test；沒有實作 supervisor；沒有執行 `git add`；沒有 merge。

## 結論：PASS

H1、H2、M1、M2、M3 都已經實質關閉。remediation 沒有引入新的 BLOCKER 或 HIGH。下方只有幾個 LOW／NOTE 的 follow-up，**不阻擋**這個 milestone。

## H1 — liveness FD 繼承：CLOSED

§3.3 規定：

- **supervisor 只能用 exec 的方式啟動：** 使用 `subprocess.Popen`，或者另外經過 review 的 `posix_spawn`。**禁止** `multiprocessing` 的 fork／forkserver，以及 bare `os.fork()`。spec 也明確寫出了「CLOEXEC 只防止 exec 時的繼承，無法防止 bare fork」，這正是我上一輪重現的機制。
- **pipe 和 ownership：** 兩條 pipe 都用 CLOEXEC 建立（`pipe2(O_CLOEXEC)` 或經過驗證的 non-inheritable API），並且有一張 **ownership 表**：
  - liveness：parent 擁有唯一的 writer，supervisor 擁有 reader，Git 兩端都沒有；
  - status：supervisor 擁有唯一的 writer，parent 擁有 reader，Git 兩端都沒有。
- **`pass_fds`：** 只能包含 liveness 的 read end 和 status 的 write end，不能包含 audit 或其他 control 的 fd。supervisor 收到之後立即設為 non-inheritable。Git 和 fake child 都使用 `close_fds=True`，而且 `pass_fds` 是空的。
- **critical window：** 從建立 pipe 開始，到 supervisor 被 reap、group 確認消失、endpoint 都關閉為止，**禁止 fork、spawn 其他 process、建立 thread，或者呼叫任何可能這樣做的 callback**。只允許 main thread，並且要求單一 thread 的 host 條件。spec 也明確承認：`threading.active_count()==1` **不足以**證明沒有 native thread 或 fork；如果 host 無法保證這個條件，就是 NOT SUPPORTED。
- **偵測 supervisor 是否死亡：** §11.1 規定 parent **同時監看 process 的結束和 status EOF**，被保留的 status writer 不能隱藏 process 的結束。
- **test：** §19 包含 fake child 的 FD 檢查，以及 `Popen(close_fds=True)` 的變體。spec 寫明這只是 harness 的壓力測試，不代表 production 可以這樣做。

## H2 — PID／PGID 重複使用，以及先 kill 再 reap：CLOSED

- **supervisor 這一側（§8.1）：**
  - leader 結束的通知**不能 reap** leader。macOS 上使用 kqueue `NOTE_EXIT`（我的 probe 證明可行）。註冊失敗時，在 reap 之前先進入 group cleanup。
  - 在完成 group-kill 的決定之前，**禁止** `poll()`、`wait()`、`communicate()`、context manager、會消耗 process 的 `waitpid`／`waitid`。
  - lifecycle 明確分為 unreaped、cleanup-decided、reaped 三個狀態；ownership 釋放之後，絕對不對記住的 PID／PGID 送出 signal。
  - 明確禁止 SIGCHLD 的自動 reap 和自訂的 reaper。這是我上一輪沒有寫到、但確實存在的路徑：`SIG_IGN` 會讓 kernel 自動 reap，ID 就會被釋放。
- **parent fallback（§11.1）：**
  - CHILD_IDENTITY 綁定 PID、PGID、SID、UID 和 kernel 回報的 start time，**不能**只用 `ps` 字串或 `kill(pid,0)`。
  - signal 的授權來自**重新驗證的身分**，並且依照一張處理表：
    - 身分完全一致 → 在 signal 之前再驗證一次，然後才 kill；
    - 身分不一致或無法判斷 → **不 kill**，分類為 `USER_ACTION_REQUIRED`；
    - leader 不存在，但有 kernel 層級的證據證明這仍然是**原本的** group → 依照 continuity 規則處理；
    - leader 不存在，只知道 group 的編號存在 → **不 kill**（編號可能已經被重複使用）。
  - spec 也誠實地寫出：start time 的 snapshot **不是**原子性的 handle，從驗證到 signal 之間的 race 必須由 adversarial test 驗證；平台如果無法證明 continuity，就是 NOT SUPPORTED。
  - 這是 fail closed 的處理：寧可留下 `USER_ACTION_REQUIRED`，也不會殺到無關的 process。

## M1 — supervisor 卡住：CLOSED

- §11.1 列出的觸發條件包括：
  - CANCEL 在期限內沒有收到 ack；
  - bounded wait 逾時；
  - 任何退出路徑上，缺少「Git group 已經消失」的有效證明。
- §11.2 規定的處理順序：
  1. SIGKILL 仍然由 parent 擁有、尚未被 reap 的 supervisor，然後在 bounded 的時間內 reap 它；
  2. 用身分綁定的方式清除 Git group；
  3. **確認 containment 之後**，才關閉 endpoint 並分類 repository 的狀態；
  4. 保留主要的錯誤，以及 cleanup 的證據。
- **CANCEL_ACK、status EOF、supervisor 結束、或者只殺死 leader，都不能取代「group 已經消失」的證明。**
- containment 無法確認時：分類為 `MUTATION_STATE_UNKNOWN / USER_ACTION_REQUIRED`，`containment_confirmed=false`，**不能正常回傳，也不能恢復 handler**。§9 第 4 和第 5 點也寫明，handler 只能在 cleanup 和 reap 完成之後才恢復。
- §14.1：containment 沒有確認時，即使 index 看起來等於 expected，也**不能**分類為 APPLIED。

## M2 — supervisor 的 runtime 和 code 綁定：CLOSED

§3.4 規定：

- **固定的啟動方式：** `[sys.executable, '-I', '-m', '<private module>']`，`shell=False`，獨立的 session，DEVNULL stdin。**cwd 必須是 repo 以外的可信、中立目錄**。module 名稱是 code 中的常數。在 `-I` 下無法解析時就 reject，**沒有 PYTHONPATH 或 cwd 的後備路徑**。
- **環境變數：** 全新的 allowlist，只有可信的 `PATH` 和 `LC_ALL=C`。不能有 HOME、`PYTHON*`、user site、`DYLD_*`、`LD_*`、credential、agent、ASKPASS 或 proxy。
- **handshake：** 綁定 protocol version、action 和 preflight 的 digest，以及**經過 review 的 code manifest**（精確的 artifact path 和 SHA-256，由可信的呼叫端固定），另外綁定 interpreter 和安裝的身分。**Action 中提供的自洽 manifest 不能作為授權的依據。**
- **supervisor 的驗證：** supervisor **獨立驗證**已載入的 module 的來源並重新計算 hash，在 READY 之前和 spawn 之前都要檢查。parent 也會驗證 READY 中的 digest。
- **邊界：** 沒有有效的 FD ownership、launch binding、protocol 和 digest handshake 時，透過 `-m`、subprocess、runpy 或直接 import 呼叫都會 fail closed，並且**不會 spawn child**。TTY、任意的 JSON、只有 digest、或 A11b.1 的 Action 都不是有效的 handshake。spec 明確禁止隱藏的 CLI、debug 路徑和 argv 選項。
- spec 誠實地把「可信的安裝」列為前提，**沒有**宣稱可以防禦同一個 UID 替換程式碼。這和 N5 的邊界一致。

## M3 — post-state 的完整分類：CLOSED

- §14.1 的前提是：containment 沒有確認時，結果一律是 `STATE_UNKNOWN / USER_ACTION_REQUIRED`。
- containment 確認之後，依照**第一個符合的規則**分類：
  1. lock 存在，或者 index 缺少、無法讀取、不安全、無效、或在讀取期間改變 → `USER_ACTION_REQUIRED`；
  2. root 或 layout 的身分漂移，或者 HEAD、attached branch 的 ref 名稱或 OID 無法讀取或和 pre 不同 → `USER_ACTION_REQUIRED`；
  3. 語意上等於 expected → exit 0 而且沒有中斷 → `APPLIED`；否則 → `APPLIED_BUT_CONTROL_INTERRUPTED`；
  4. 語意上等於 pre，**而且 pre 不等於 expected** → `NOT_APPLIED`（即使 exit 是 0）；
  5. 其他 → `USER_ACTION_REQUIRED`，搭配 `MUTATION_STATE_UNKNOWN`。
- **no-op**（expected 等於 pre）在第 3 條就會被處理，**不可能**被分類為 NOT_APPLIED，所以不再有歧義。
- **raw index 從來不是分類的條件**，只作為鑑識用的證據。這和 B4 一致。
- HEAD 和 branch 在 post 時重新驗證（第 2 條）。主要的 lifecycle 失敗和語意上的分類分開記錄。

## 相關的要求：都已經納入

| 項目 | 確認 |
|---|---|
| L1 | §4：4 bytes 的 big-endian 長度前綴，最大 128 KiB；長度為 0 或過大時，在配置記憶體之前就拒絕；嚴格的 fixed-schema JSON（拒絕重複的 key、未知的欄位、非有限的數字，深度不超過 8，值的數量不超過 4096）；non-blocking 搭配 select／kqueue 和 deadline；處理 partial read 和 write；**訊息讀到一半遇到 EOF 一律視為 cancel**；未知的 version 或 type 會 fail closed；EPIPE 不會中斷 cleanup；不使用 pickle 或 eval |
| L2 | §11.3：明確承認這個空窗**沒有被消除**；identity 必須立即送出；缺少 identity 時視為 unknown containment，不能猜測 PGID，也不能宣稱已經 cancel；必須有「在送出之前讓 supervisor 死掉」的 fault injection |
| L3 | §9：在建立 pipe 和 spawn 之前安裝 handler；handler 只記錄狀態；在 cleanup 和 reap 完成之後才恢復；重複的 signal 無法繞過 cleanup；寫明「安全性來自 EOF，handler 只提供 typed outcome」 |
| N1 | §19：在真正的 Mac 上、sandbox 以外執行；包括 plain 和 `Popen(close_fds=True)` 兩種變體；**每一種至少 20 次**；cleanup 的延遲不超過 2 秒；用 `kill(pid,0)` 和 `killpg(pgid,0)` 判斷；記錄 OS、Python 和 code digest |
| N4 | §16 和 §21：durable intent 必須在 product action 之前完成；private engine 不是 bypass；用 AST、動態 import、`-m`、subprocess、runpy 和間接的 wrapper 掃描邊界 |

## LOW／NOTE follow-up（不阻擋，在 2.2.1 的實作和 review 中處理）

- **F1（LOW，文字張力）：**
  - §9 的 Rules 寫著「signal handlers are restored on every exit path」，但 §11.2 和 §9 的最後一段寫著「containment 沒有確認時，不能走一般的恢復和回傳路徑」。
  - 兩者的意圖是一致的：在確認之前不恢復。但 2.2.1 的 harness 必須**具體定義**「containment 沒有確認」時，engine 實際的退出行為。例如：丟出一個特定的 `ContainmentUnresolved` 結果，其中包含 PID、PGID 和 start time 的證據；handler 是否保留；harness 和 CLI 如何把這個狀態呈現給操作者。
- **F2（NOTE）：** kqueue `NOTE_EXIT` 對活著的 leader 和已經是 zombie 的 leader 都可以正確回報，而且不會 reap（本輪的 probe）。2.2.1 應該把這兩種情況寫成正式的 test，並且加入註冊失敗的路徑。
- **F3（NOTE）：** macOS 上 kernel start time 的取得方式（例如 `sysctl` 的 `KERN_PROC_PID`，透過 ctypes），以及 continuity 的程序，必須在 2.2.1 中實作，並用「模擬 ID 被重複使用」和「驗證到 signal 之間的 race」做 adversarial test。spec 已經規定，平台無法證明時就是 NOT SUPPORTED。
- **F4（NOTE）：** 「單一 thread 的 host」是一個前提。2.2.1 應該說明 llmtool 的 parent（將來由 A11b.3 呼叫）如何滿足這個條件，例如哪些模組可能建立 thread，以及進入 mutation 之前的檢查方式。

## 判定

```text
A11b.2.2 B5 TARGETED DESIGN RE-REVIEW:
PASS

Reviewed remediation head:
2335e842ba88d41bae51010f998ff7abdec338f2

H1:
CLOSED — exec-only supervisor launch, CLOEXEC pipes with ownership table, pass_fds limited to liveness-read/status-write,
  no control/liveness/status/audit FD in Git/fake children, no fork/spawn/thread/callback in the critical window
  (main thread + reviewed single-thread host, NOT SUPPORTED otherwise), supervisor death detected by process exit as
  well as status EOF

H2:
CLOSED — non-reaping kqueue NOTE_EXIT exit detection (probe-confirmed on macOS for live and zombie leaders), no
  poll/wait/communicate/context-manager/consuming waitpid before the group-kill decision, SIGCHLD auto-reap forbidden,
  parent fallback bound to PID/PGID/SID/UID/kernel start time with fresh verification; mismatch, ambiguity or
  numeric-only group existence never signals; continuity race must be tested or the platform is NOT SUPPORTED

M1:
CLOSED — missing ACK / wait expiry / missing group-gone proof → SIGKILL + reap owned supervisor, identity-bound Git
  cleanup, classification only after confirmed containment; no ordinary return or handler restoration while owned
  processes may live; unresolved → MUTATION_STATE_UNKNOWN/USER_ACTION_REQUIRED, containment_confirmed=false

M2:
CLOSED — fixed [sys.executable, -I, -m, private module], trusted neutral cwd, PATH/LC_ALL-only env, reviewed code
  manifest + interpreter/installation identity in handshake, supervisor-side origin/hash verification before READY
  and before spawn, invalid -m/subprocess/runpy/import invocation fails closed without child spawn, no hidden CLI

M3:
CLOSED — containment prerequisite + first-match table (lock/index → HEAD/branch/root drift → expected → pre≠expected
  → other); no-op resolved via the expected-state row; raw index evidence-only; post HEAD/branch revalidation

New BLOCKER/HIGH findings:
NONE

LOW/NOTE follow-ups:
F1 (LOW) define the concrete unresolved-containment exit behaviour, reconciling §9 "restored on every exit path" with
  §11.2 "no restoration while containment unconfirmed"; F2 kqueue live/zombie/registration-failure tests;
  F3 macOS kernel start-time facility + continuity/race adversarial tests; F4 document how the parent host satisfies
  the single-thread premise

May merge PR #44 if final-head CI passes:
YES

May implement A11b.2.2.0 after merge:
YES

May implement A11b.2.2.1 after 2.2.0:
YES

May implement real git add A11b.2.2.2:
NO
```

**本輪 targeted re-review 到此結束；不因 LOW／NOTE 延長 B5 design gate。**

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #44 的 branch。沒有修改 spec、README、ROADMAP、production 或 test，沒有 merge。
- probe 只在 session 的 scratchpad 中執行，沒有留下任何 process，也沒有加入 Git。
