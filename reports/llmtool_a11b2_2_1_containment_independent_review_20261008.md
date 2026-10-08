# llmtool repo review — PR #49 A11b.2.2.1 containment harness：獨立 security review 與 Mac acceptance 重新驗證（2026-10-08）

## Repo 狀態（`main @ ceae425`）

- **已經 merge：**
  - PR #44：B5 containment spec，包括 remediation；
  - PR #46：A11b.2.2.0 read-only preflight cleanup（處理了 A11b.2.1 的 L1 和 L2）；
  - PR #47、#48：test infra 的 timeout 調整。
- **open draft：**
  - **PR #49：** A11b.2.2.1 的 fake-containment harness，「supervisor group anchor」版本，head `acc4582`；
  - **PR #45：** ai-loop 自動化（+9,660 行，16 個檔案，包括 `.github/workflows/ai-loop-ci.yml` 和 `scripts/ai_loop.py`）。

## PR #49 的審查範圍和方法

- **branch：** `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`，head **`acc458272fa93814bcb8dca9e7acd0d0d93fedc2`**。
- **production 程式碼：** 5 個 private module，共 1,524 行：
  - `_git_containment.py`；
  - `_git_supervisor.py`；
  - `_git_containment_protocol.py`；
  - `_git_process_identity.py`；
  - `_git_containment_child.py`。
- **test：** `test_git_containment.py`（2,655 行）、Mac 的 acceptance runner、probe host。
- **逐行閱讀：** 4 個 production module 的全文，以及 protocol 的前 200 行和 binding 的部分。對照 B5 spec（main 上的 remediation 版本）和 F3 recovery validation report。
- **執行（在真正的 Mac 上，sandbox 以外）：**
  - `test_git_containment`：**79／79 OK**（venv 的 Python 3.13，以及 `/usr/bin/python3` 3.9.6）；`test_git_mutation`：OK（skipped=2）；
  - **完整的 14 個 native acceptance 情境**（見下方）；
  - 執行前後 tracked tree 都是 clean 的，**沒有留下任何 supervisor、child 或 host process**（`pgrep` 確認）。
- **CI：** PR checks 的 3.9 和 3.14（run 37782779810 和 37782786876）**全部 pass**。
- **沒有做的事：**
  - 沒有修改 production 或 test；
  - 沒有執行真正的 `git add`；
  - 沒有開始 A11b.2.2.2；
  - 沒有 merge；
  - 沒有審查 PR #45 的內容。

## Mac acceptance 的重新驗證：PASS（處理 PR #49 的「MAC ACCEPTANCE REVALIDATION REQUIRED」）

- **環境：** macOS **15.7.9**、Python **3.13.3**（`tools/llmtool/.venv`）；canonical repo，cwd 是 repo root；`PYTHONPATH=tools/llmtool:tools/llmtool/tests`。
- **命令：** `run_containment_acceptance.py --output <scratchpad>/evidence.json`，**exit 0**。
- **contract 的說明：** runner 需要和 output 放在同一個目錄的 `task.json`。這份檔案原本存在 ai-loop 的 `.ai-loop/` 目錄中，所以我**依照 runner 本身固定的常數**在 scratchpad 中建立了一份等價的 contract：task_id、base `ceae425`、`EXPECTED_FILES` 中的 10 個 allowed／required 檔案。我沒有放寬任何檢查，runner 仍然會驗證這些常數。
- **結果：** 14 個情境**全部 PASS**，tested 的 9 個檔案在執行前後的 hash 都沒有改變。evidence 的 SHA-256 是 `1b68f7d7…2fcd`（保存在 scratchpad，沒有 commit）。

| 情境 | checks | 備註 |
|---|---:|---|
| fd_inheritance | 9 | |
| sigint／sigterm／sighup | 25／25／25 | 真實的 signal |
| hung_supervisor | 112 | |
| leader_exit_descendant | 8 | |
| identity_reuse | 7 | |
| macos_start_time | 5 | |
| kqueue_non_reaping | 74 | 包括 `NOTE_EXIT`（live）和 `ESRCH_AFTER_OWNED_ZOMBIE`（先變成 zombie），兩條路徑都沒有 reap |
| protocol_framing | 18 | |
| code_runtime_binding | 7 | |
| public_import_boundary | 110 | 有一個無害的 runpy `RuntimeWarning` |
| **parent_sigkill_plain** | 263 | **20 次**，最大的 cleanup 延遲是 **0.0115 秒** |
| **parent_sigkill_popen** | 2,895 | **20 次**（包括 H1 的 `Popen(close_fds=True)` 變體），最大的延遲是 **0.0121 秒** |

兩個 SIGKILL 情境的延遲都**遠低於** spec 要求的 2 秒。

## 架構：supervisor 作為 group anchor

這一版**偏離了 main 上 B5 spec 原本的拓撲**（spec 要求 Git 有獨立的 session 和 group，parent fallback 用 Git 的身分來判斷）：

- supervisor 是新的 session leader，PID == PGID == SID。fake Git（leader）和 descendant **都繼承 supervisor 的 group**，不會改變 session 或 group。
- **parent 的 cleanup：** parent 驗證 supervisor 是**自己尚未 reap 的直接子 process**（`Popen.returncode is None`、PPID、kernel 的 start time 等），然後才 `killpg(supervisor pgid)`，最後才 reap。
  - 這時 PGID 被 supervisor（活著或 zombie）**佔住**，所以不會被重複使用；
  - macOS 上，zombie leader 加上活著的成員時，killpg 是有效的（我在 B5 review 的 K-A probe 已經驗證）。
- **parent 死掉時：** supervisor 收到 EOF，最後一個動作是 `killpg` 自己的 group，連自己也一起殺死。

我的評估：這個設計**比 spec 原本的「parent 用 Git 孫 process 的身分做 fallback」更強**，因為它避開了孫 process 被 launchd reap 之後 ID 被重複使用的問題。B5 的 H2 在這個拓撲下自然成立。

**H1、M1、M2、F1–F4 在程式碼中的對應：**

- **H1：** pipe 被設為 non-inheritable，並且會檢查；`pass_fds` 只有 `(control_r, status_w)`，spawn 之後 parent 立即關閉這兩個 fd。supervisor 的 `_endpoints()` 要求剛好 2 個 FIFO（一個 RDONLY、一個 WRONLY），而且 dev 和 ino 必須和 START 中記錄的一致。child 使用 `close_fds=True` 和空的 `pass_fds`。
- **M1：** 沒有收到 ACK 或逾時 → `kill_group`；group 消失沒有被證明 → `ContainmentUnresolved`。
- **M2：** 啟動方式是 `[sys.executable,'-I','-m','llmtool._git_supervisor']`，cwd 是 `/private/tmp` 下中立的暫存目錄，環境變數是 `{'PATH':'/usr/bin:/bin','LC_ALL':'C'}`。binding 包含 artifact 的 manifest 和 interpreter 的 hash，supervisor 在 READY 之前和 spawn 之前各驗證一次。
- **F1：** `ContainmentUnresolved` 是 BaseException，會保留 handler、fd、Popen 和 note。host 進入 operator-wait 狀態，不會正常回傳。
- **F2：** kqueue 不會 reap，而且有 native 的 test。
- **F3：** `Anchor` 的 lifecycle 分為 verify、kill_group（decision）、reap，reap 之前必須已經做出決定，否則拒絕。
- **F4：** `_validate_host` 只允許固定的 `tests/containment_probe_parent.py`，而且必須是 isolated、沒有 trace 或 profile、hash 正確。`host_check` 檢查 main thread、`active_count==1`、native 的 thread 數為 1（`proc_taskinfo.pti_threadnum`，我核對過欄位的位置是正確的）、SIGCHLD 是 SIG_DFL。
- **libproc 的 ABI：** `_BSD`（`proc_bsdinfo`）和 `_Task`（`proc_taskinfo`）的欄位順序，我和 SDK 的定義逐一核對過，都是正確的。

## Findings

### P49-M1 — MEDIUM — supervisor 的「last act」在身分查詢失敗時，會不殺 group 就直接結束（建議在 merge 之前修正）

- **位置：** `_git_supervisor.py` 的 `_last_act`（第 44–56 行）。
- **問題：** 在收到 parent 的 EOF、CANCEL，或者發生任何例外之後，supervisor 會先呼叫 `identity(os.getpid())`（透過 libproc 和 getsid）。如果**呼叫失敗**，或者結果和 launch 時的身分**不一致**，它會執行 `os._exit(79)`，**完全不會 killpg**。
- **影響：** 這正是 parent 已經死亡、唯一能清除 Git 的路徑。在這條路徑上，一次暫時性的 libproc 失敗（例如資源不足、fd 用盡、或 sandbox 的限制），會讓 fake Git 或 descendant 變成**孤兒，繼續執行**。在 A11b.2.2.2 中，這會是真正的 `git add`。
- **為什麼不需要這個檢查：** supervisor 是**活著的 session leader**，而且從來不呼叫 `setsid` 或 `setpgid`。**一個活著的 process 自己的 group ID 不可能被重複使用**。`os.killpg(0, SIGKILL)`（對呼叫者自己的 group 送出 signal），或者先用 `os.getpgrp()` 確認，就可以**完全避開「數字 ID 不明確」的問題**。現在的身分檢查並沒有讓這一步更安全，反而**把一個不明確的查詢結果變成 fail-open**（放任 child 繼續執行）。
- **修正：** 在 `_last_act` 中，只要 `os.getpgrp() == os.getpid()`（不需要 libproc），就執行 `os.killpg(0, signal.SIGKILL)`，然後 `os._exit`。只有在 `getpgrp` 不等於 pid（理論上不會發生）的情況下，才不送出 signal。加入一個 test：用 monkeypatch 讓 `identity` 丟出例外，確認 group 仍然會被殺死。
- **是否阻擋：** 建議在 PR #49 merge 之前修正。這是一個很小的修改，會改變 tested hash，所以 Mac acceptance 需要再跑一次（大約 40 秒）。**一定要在 A11b.2.2.2 之前處理。**

### P49-M2 — MEDIUM — main 上的 B5 spec 和實作的拓撲不一致（文件；在 A11b.2.2.2 之前必須處理）

- **問題：** main 上的 B5 spec（PR #44 的 remediation 版本），§3.2 第 2 項、§6（Git 使用 `start_new_session=True`）、§11.1（parent 依照 Git leader 的身分做 fallback）都仍然描述**舊的拓撲**。
- 實作改成 supervisor group anchor 的依據是 validation report 中寫的「Section 8 of the recovery handoff supersedes the old separate-child-group …」。那份 handoff 位於 ai-loop 的 `.ai-loop/…/task.json`，**不在 repo 的 main 中**。
- **影響：** 將來的 A11b.2.2.2 審查者會依照 main 上的 spec 判斷，所以會看到兩者不一致。這次的授權依據也無法從 repo 本身追溯。
- **修正：** 用一份**只修改文件的 spec amendment** 寫明以下內容：
  - supervisor 作為 group anchor 的拓撲；
  - 「parent 只對自己尚未 reap 的 supervisor group 送出 signal」的規則；
  - supervisor 的 last-act 語意（包括 P49-M1 的 `killpg(0)`）；
  - 原本 §11.1 的孫 process 身分 fallback **改為不需要**。

  並且把 recovery handoff §8 的授權內容寫進 repo。

### P49-L1 — LOW — fault injection 的分支在 production module 中

- `_git_containment._run` 中的 `case == 'unresolved'`（刻意竄改 anchor 的 micros）和 `'anchor_race'`，以及 `_git_supervisor` 中的 `hung`、`malformed_status`、`closed_status`、`lost_before_identity`、`registration_failure`、`drain_failure`，都寫在 production module 裡。
- 現在 `_validate_host` 只允許固定的 test host，所以**沒有被利用的路徑**。但是 A11b.2.2.2 的 product engine **不能帶著這些 hook**。建議 2.2.2 的設計明確要求把它們分開，例如改成 test-only 的 subclass 或 fixture module，並且用 import 邊界的 test 確認 product 的路徑無法觸及這些 case。

### P49-L2 — LOW — 會在 import ctypes 時保留 fd 的 runtime（例如 uv Python），supervisor 會 fail closed

- supervisor 先執行 `host_check()`（這時會 import ctypes 並載入 libproc），然後才呼叫 `_endpoints()`。而 `_endpoints()` 要求 fd 大於 2 的只能有 2 個 FIFO。
- PR 的說明已經記錄：uv 的 Python 3.14 在 `import ctypes` 時會保留一個指向 runtime binary 的 fd。在這種 runtime 上，supervisor 會因為多出來的這個 fd 而 reject，結果是 `start-failed`。這是 **fail closed**，不是安全性的問題。
- 建議在支援的 runtime 清單中寫明這一點，或者讓 `_endpoints()` 在 `host_check()` **之前**執行。

### P49-L3 — LOW — reap 之後確認 group 消失時，使用的是數字 ID

- `_group_gone` 在 reap 之後，用 `killpg(pgid, 0)` 或 `kill(pid, 0)` 被動地確認。如果在這段期間 ID 被重複使用，結果會是「尚未消失」，進而變成 `ContainmentUnresolved`。
- 這是**偏向安全的誤判**，不會誤殺任何 process。在 PID 快速循環的高負載環境下，可能偶爾造成 flaky，這一點可以接受。

### NOTE

- **N1：** F1 的解決路徑其實很乾淨。host 停在 operator-wait 時，**操作者 SIGKILL 這個 host → supervisor 收到 EOF → last act 殺死整個 group**。但這條路徑依賴 P49-M1 的修正，否則可能因為 libproc 的失敗而放過 child。建議把這條路徑寫進操作說明。
- **N2：** PR #49 的歷史中有 16 個 commit，包括自動化 loop 的多個「round N」commit 和兩個 merge commit。建議用 squash 的方式 merge，並在 squash message 中保留最終的 tested head 和 Mac evidence 的 digest。
- **N3（PR #45）：** ai-loop 的自動化 PR 新增了 CI workflow 和自主實作與 review 的 script（+9,660 行）。**它會影響之後所有 PR 的產生和驗證方式**，建議在 merge 之前做一次**獨立的 security review**，重點包括：workflow 的權限和 token、能否自行 push 或 merge、Mac gate 是否可以被偽造（例如 runner 讀取的 `task.json` 和 evidence 的來源）、以及 secret 的處理。這次沒有審查它。

## 建議的下一步

1. **PR #49：** 修正 P49-M1（一個小的 commit，加上 test）→ 在 Mac 上重跑 14 個 acceptance 情境（我可以重跑）→ 寫 P49-M2 的 spec amendment（可以放在同一個 PR 或另一個 docs PR）→ squash merge。
2. **A11b.2.2.2（真正的 `git add`）：** 繼續保持 **NO**，直到：
   - PR #49 merge；
   - spec amendment 完成；
   - 2.2.2 的設計明確處理 P49-L1（fault hook 的分離），以及 B5 M3 的完整分類表。
3. **PR #45：** 先做 security review，再決定是否 merge。

## 判定

```text
REPO REVIEW 2026-10-08 — PR #49 A11b.2.2.1 CONTAINMENT HARNESS:
APPROVE WITH CHANGES

Reviewed head:
acc458272fa93814bcb8dca9e7acd0d0d93fedc2

Native Mac acceptance (revalidation):
PASS — 14/14 scenarios, exit 0, macOS 15.7.9 / Python 3.13.3, tested hashes unchanged; parent SIGKILL plain/Popen
  20+20 repetitions, max cleanup latency 0.012 s; no residual processes; tracked tree clean

Unit tests / CI:
PASS — test_git_containment 79/79 (3.13, 3.9.6); test_git_mutation OK; PR CI 3.9/3.14 pass on acc4582

Architecture (supervisor group anchor):
SOUND — stronger than the spec's grandchild-identity fallback; H1/M1/M2/F1–F4 mapped in code; libproc ABI checked

Findings:
P49-M1 MEDIUM — supervisor last-act exits WITHOUT killing its own group if libproc identity fails/mismatches (fail-open
  on the parent-dead path); use killpg(0)/getpgrp self-group kill; fix + Mac rerun before merge
P49-M2 MEDIUM — B5 spec on main still describes the old separate-Git-session topology; authorization lives in an ai-loop
  handoff outside the repo; doc-only spec amendment before A11b.2.2.2
P49-L1 LOW — fault-injection branches inside production modules; separate before product engine
P49-L2 LOW — ctypes-FD runtimes (uv 3.14) fail closed at _endpoints; document or reorder
P49-L3 LOW — numeric post-reap absence check can false-alarm into ContainmentUnresolved (safe)

Ready to merge PR #49:
NO — after P49-M1 fix + Mac acceptance rerun (P49-M2 may follow in a docs PR before 2.2.2)

May implement real git add (A11b.2.2.2):
NO

PR #45 (ai-loop automation):
NOT REVIEWED — independent security review recommended before merge
```

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #49 的 branch，**只新增了 `reports/` 底下的檔案**，所以不會影響 runner 固定的 tested 檔案 hash。沒有修改 production 或 test，沒有 merge，也沒有開始 A11b.2.2.2。
- acceptance 的 evidence 和自己建立的 contract 都只放在 session 的 scratchpad 中。
